from decimal import Decimal, InvalidOperation
import decimal
from collections import defaultdict
from types import SimpleNamespace
from django.db import transaction
from .models import Group, Participant, Expense, ExpenseSplit, Payment, user_display_name
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.views.decorators.http import require_POST

User = get_user_model()


def _sidebar_groups_qs(user):
    """QS padrão para preencher a sidebar com os grupos do usuário."""
    return (
        Group.objects
        .filter(Q(participants__user=user) | Q(creator=user))
        .distinct()
        .order_by("-created_at")
        .prefetch_related("participants__user")
    )


def _my_participant(group, user):
    """O Participant que representa `user` dentro de `group`, se houver."""
    return group.participants.filter(user=user).first()


def _can_manage(group, user):
    """Quem pode registrar despesas e pagamentos em nome de terceiros."""
    return group.creator_id == user.id


def _balances_for_groups(group_ids):
    """
    Saldos de vários grupos de uma vez, indexados por Participant.id.

    Faz três consultas no total, independente de quantos grupos entrarem. A
    versão por grupo custava três consultas *cada*, o que pesa muito quando o
    banco está longe da aplicação e cada ida e volta custa latência de rede.
    """
    group_ids = list(group_ids)
    balances = {group_id: defaultdict(Decimal) for group_id in group_ids}

    if not group_ids:
        return {}

    despesas = Expense.objects.filter(group_id__in=group_ids).values_list(
        'group_id', 'paid_by_id', 'amount'
    )
    for group_id, payer_id, amount in despesas:
        balances[group_id][payer_id] += amount

    divisoes = ExpenseSplit.objects.filter(
        expense__group_id__in=group_ids
    ).values_list('expense__group_id', 'participant_id', 'amount_owed')
    for group_id, participant_id, amount_owed in divisoes:
        balances[group_id][participant_id] -= amount_owed

    pagamentos = Payment.objects.filter(group_id__in=group_ids).values_list(
        'group_id', 'payer_id', 'receiver_id', 'amount'
    )
    for group_id, payer_id, receiver_id, amount in pagamentos:
        balances[group_id][payer_id] += amount
        balances[group_id][receiver_id] -= amount

    return {group_id: dict(saldos) for group_id, saldos in balances.items()}


def _participants_for_groups(group_ids):
    """Participantes de vários grupos de uma vez, numa consulta só."""
    group_ids = list(group_ids)
    por_grupo = {group_id: [] for group_id in group_ids}

    if not group_ids:
        return {}

    consulta = Participant.objects.filter(
        group_id__in=group_ids
    ).select_related('user').order_by('id')

    for participante in consulta:
        por_grupo[participante.group_id].append(participante)

    return por_grupo


def _calculate_balances(group):
    """Saldo de cada participante do grupo, indexado por Participant.id."""
    return _balances_for_groups([group.id])[group.id]

def _calculate_settlements(balances, participants_qs):
    participant_map = {p.id: p for p in participants_qs}
    creditors = []
    debtors = []

    for participant_id, balance in balances.items():
        if balance > Decimal("0.01"):
            creditors.append([participant_id, balance])
        elif balance < Decimal("-0.01"):
            debtors.append([participant_id, -balance])

    settlements = []
    i = j = 0

    while i < len(creditors) and j < len(debtors):
        creditor_id, credit = creditors[i]
        debtor_id, debt = debtors[j]
        transfer = min(credit, debt).quantize(Decimal("0.01"))

        settlements.append(SimpleNamespace(
            person_from=participant_map[debtor_id],
            person_to=participant_map[creditor_id],
            amount=transfer,
        ))

        creditors[i][1] -= transfer
        debtors[j][1] -= transfer

        if creditors[i][1] < Decimal("0.01"):
            i+=1
        if debtors[j][1] < Decimal("0.01"):
            j+=1

    return settlements

def _my_debts_snapshot(user):
    """
    Pendências e pagamentos do ponto de vista de `user`.

    Em grupos gerenciados, o criador enxerga também as pendências entre participantes
    avulsos, já que é ele quem registra esses acertos.
    """
    pending_to_pay = []
    pending_to_receive = []
    managed_pending = []
    paid = []

    sidebar_groups = list(_sidebar_groups_qs(user))
    group_ids = [group.id for group in sidebar_groups]

    # Tudo em lote: o custo não cresce mais com a quantidade de grupos.
    balances_by_group = _balances_for_groups(group_ids)
    participants_by_group = _participants_for_groups(group_ids)

    payments_by_group = defaultdict(list)
    for payment in Payment.objects.filter(
        group_id__in=group_ids
    ).select_related('payer__user', 'receiver__user'):
        payments_by_group[payment.group_id].append(payment)

    for group in sidebar_groups:
        participants = participants_by_group.get(group.id, [])
        balances = balances_by_group.get(group.id, {})
        settlements = _calculate_settlements(balances, participants)
        is_manager = _can_manage(group, user)

        for settlement in settlements:
            if settlement.person_from.user_id == user.id:
                pending_to_pay.append({
                    "group": group,
                    "counterparty": settlement.person_to,
                    "amount": settlement.amount,
                    "payer": settlement.person_from,
                })
            elif settlement.person_to.user_id == user.id:
                pending_to_receive.append({
                    "group": group,
                    "counterparty": settlement.person_from,
                    "amount": settlement.amount,
                })
            elif is_manager:
                managed_pending.append({
                    "group": group,
                    "payer": settlement.person_from,
                    "counterparty": settlement.person_to,
                    "amount": settlement.amount,
                })

        meus_ids = {p.id for p in participants if p.user_id == user.id}

        for payment in payments_by_group.get(group.id, []):
            envolve_voce = payment.payer_id in meus_ids or payment.receiver_id in meus_ids
            if not (is_manager or envolve_voce):
                continue
            setattr(payment, "can_undo", _can_undo_payment(group, payment, user))
            paid.append(payment)

    return {
        "pending_to_pay": pending_to_pay,
        "pending_to_receive": pending_to_receive,
        "managed_pending": managed_pending,
        "paid": sorted(paid, key=lambda p: p.created_at, reverse=True),
        "balances_by_group": balances_by_group,
        "participants_by_group": participants_by_group,
    }


@login_required
def group_list(request):
    groups = _sidebar_groups_qs(request.user)
    return render(request, "rachais/group_list.html", {"groups": groups})


@login_required
def group_detail(request, group_id):
    groups = _sidebar_groups_qs(request.user)
    group = get_object_or_404(groups, pk=group_id)

    expenses = list(group.expenses.select_related("paid_by__user").all())

    total = sum((e.amount for e in expenses), Decimal("0"))

    # O snapshot da sidebar já calcula saldos e participantes de todos os grupos
    # em lote — reaproveitamos os deste grupo em vez de consultar de novo.
    my_debts = _my_debts_snapshot(request.user)
    participants = my_debts["participants_by_group"].get(group.id) or list(
        group.participants.select_related("user")
    )
    balances = my_debts["balances_by_group"].get(group.id, {})

    settlements = _calculate_settlements(balances, participants)

    for participant in participants:
        balance = balances.get(participant.id, Decimal("0"))
        setattr(participant, "balance", balance)
        setattr(participant, "balance_abs", balance.copy_abs())

    # Saldos agrupados: primeiro quem tem a receber (do maior para o menor),
    # depois quem tem a pagar (da maior dívida para a menor) e, por fim, os quites.
    balance_rows = (
        sorted((p for p in participants if p.balance > 0), key=lambda p: -p.balance)
        + sorted((p for p in participants if p.balance < 0), key=lambda p: p.balance)
        + [p for p in participants if p.balance == 0]
    )

    all_splits = ExpenseSplit.objects.filter(
        expense__in=expenses
    ).select_related('participant__user')

    splits_by_expense = defaultdict(list)
    for split in all_splits:
        splits_by_expense[split.expense_id].append(split)

    for e in expenses:
        setattr(e, "can_edit", _can_edit_expense(group, e, request.user))

        split_list = []
        splits_for_this_expense = splits_by_expense.get(e.id, [])

        for split in splits_for_this_expense:
            if split.participant_id == e.paid_by_id:
                continue

            split_list.append({
                "name": split.participant.display_name,
                "amount": split.amount_owed
            })

        setattr(e, "split_list", split_list)

    return render(
        request,
        "rachais/group_detail.html",
        {
            "group": group,
            "groups": groups,
            "expenses": expenses,
            "participants": participants,
            "balance_rows": balance_rows,
            "total": total,
            "settlements": settlements,
            "my_debts": my_debts,
            "can_manage": _can_manage(group, request.user),
        }
    )

@login_required
def create_group(request):
    """Cria um grupo; impede nomes repetidos apenas para o MESMO criador."""
    groups = _sidebar_groups_qs(request.user)
    valid_modes = {choice[0] for choice in Group.MODE_CHOICES}

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        mode = (request.POST.get("mode") or Group.MODE_SHARED).strip()

        if mode not in valid_modes:
            mode = Group.MODE_SHARED

        if not name:
            messages.error(request, "Informe um nome para o grupo.")
        elif len(name) > 100:
            messages.error(request, "O nome deve ter no máximo 100 caracteres.")
        elif Group.objects.filter(creator=request.user, name__iexact=name).exists():
            messages.error(request, "Você já tem um grupo com esse nome.")
        else:
            new_group = Group.objects.create(name=name, creator=request.user, mode=mode)
            Participant.objects.get_or_create(group=new_group, user=request.user)
            messages.success(request, "Grupo criado com sucesso!")
            return redirect("rachais:group_detail", group_id=new_group.id)

        return render(
            request,
            "rachais/create_group.html",
            {"groups": groups, "name": name, "mode": mode, "modes": Group.MODE_CHOICES},
        )

    return render(
        request,
        "rachais/create_group.html",
        {"groups": groups, "mode": Group.MODE_SHARED, "modes": Group.MODE_CHOICES},
    )


@login_required
def add_participant(request, group_id):
    """
    Adiciona um participante ao grupo.

    Em grupos colaborativos, procura um usuário cadastrado pelo username ou e-mail.
    Em grupos gerenciados, basta o nome: o participante existe só dentro do grupo.
    """
    group = get_object_or_404(Group, pk=group_id)

    if request.user != group.creator:
        messages.error(request, "Apenas o criador do grupo pode adicionar participantes.")
        return redirect("rachais:group_detail", group_id=group.id)

    if request.method != "POST":
        return redirect("rachais:group_detail", group_id=group.id)

    if group.is_managed:
        guest_name = (request.POST.get("guest_name") or "").strip()

        if not guest_name:
            messages.error(request, "Informe o nome do participante.")
            return redirect("rachais:group_detail", group_id=group.id)

        if len(guest_name) > 100:
            messages.error(request, "O nome deve ter no máximo 100 caracteres.")
            return redirect("rachais:group_detail", group_id=group.id)

        if group.participants.filter(guest_name__iexact=guest_name).exists():
            messages.info(request, "Já existe um participante com esse nome no grupo.")
            return redirect("rachais:group_detail", group_id=group.id)

        Participant.objects.create(group=group, guest_name=guest_name)
        messages.success(request, f"{guest_name} adicionado(a) com sucesso!")
        return redirect("rachais:group_detail", group_id=group.id)

    ident = (request.POST.get("identifier") or "").strip()

    if not ident:
        messages.error(request, "Informe o username ou e-mail do usuário.")
        return redirect("rachais:group_detail", group_id=group.id)

    try:
        user = User.objects.get(username__iexact=ident)
    except User.DoesNotExist:
        try:
            user = User.objects.get(email__iexact=ident)
        except User.DoesNotExist:
            user = None

    if not user:
        messages.error(request, "Usuário não encontrado.")
        return redirect("rachais:group_detail", group_id=group.id)

    if Participant.objects.filter(group=group, user=user).exists():
        messages.info(request, "Esse usuário já está no grupo.")
        return redirect("rachais:group_detail", group_id=group.id)

    Participant.objects.create(group=group, user=user)
    messages.success(request, f"{user_display_name(user)} adicionado(a) com sucesso!")
    return redirect("rachais:group_detail", group_id=group.id)

def _can_edit_expense(group, expense, user):
    """
    Quem pode mexer numa despesa já lançada.

    O criador do grupo sempre pode — inclusive nas despesas de participantes
    avulsos, que não têm login. Fora isso, cada um corrige o que pagou.
    """
    if _can_manage(group, user):
        return True
    return expense.paid_by.user_id == user.id


def _expense_only_balances(group):
    """Saldos considerando apenas as despesas, sem os pagamentos já registrados."""
    balances = defaultdict(Decimal)
    expenses = list(group.expenses.all())

    for expense in expenses:
        balances[expense.paid_by_id] += expense.amount

    for split in ExpenseSplit.objects.filter(expense__in=expenses):
        balances[split.participant_id] -= split.amount_owed

    return balances


def _prune_orphan_payments(group):
    """
    Desfaz pagamentos que ficaram sem dívida por trás.

    Um pagamento só existe para quitar uma dívida vinda das despesas. Quando uma
    despesa é apagada — ou tem o valor reduzido —, parte desses acertos pode
    perder o lastro; sozinhos, eles passariam a gerar dívidas no sentido
    contrário (quem pagou apareceria como credor do nada). Aqui removemos os
    acertos mais recentes até que o que sobrou caiba dentro das dívidas reais.

    Devolve quantos pagamentos foram desfeitos.
    """
    tolerance = Decimal("0.01")
    removed = 0

    while True:
        payments = list(group.payments.order_by("-created_at", "-id"))
        if not payments:
            break

        expense_balances = _expense_only_balances(group)

        moved = defaultdict(Decimal)
        for payment in payments:
            moved[payment.payer_id] += payment.amount
            moved[payment.receiver_id] -= payment.amount

        unsupported = set()
        for participant_id, net in moved.items():
            owed = expense_balances.get(participant_id, Decimal("0"))
            final = owed + net

            # O acerto só pode empurrar a pessoa em direção ao zero: nunca além
            # do tamanho da dívida, nem para o outro lado dela.
            if abs(final) > abs(owed) + tolerance:
                unsupported.add(participant_id)
            elif owed > tolerance and final < -tolerance:
                unsupported.add(participant_id)
            elif owed < -tolerance and final > tolerance:
                unsupported.add(participant_id)

        if not unsupported:
            break

        target = next(
            (
                p for p in payments
                if p.payer_id in unsupported or p.receiver_id in unsupported
            ),
            None,
        )
        if target is None:
            break

        target.delete()
        removed += 1

    return removed


def _warn_about_pruned_payments(request, removed):
    if not removed:
        return
    if removed == 1:
        messages.info(request, "1 pagamento foi desfeito por ter ficado sem dívida correspondente.")
    else:
        messages.info(
            request,
            f"{removed} pagamentos foram desfeitos por terem ficado sem dívida correspondente.",
        )


def _splits_equal(amount, participants):
    """Divide o valor em partes iguais; a sobra dos centavos fica com o primeiro."""
    if not participants:
        return None, "Não há participantes no grupo para dividir."

    split_amount = round(amount / len(participants), 2)
    remainder = amount - (split_amount * len(participants))

    splits = []
    for i, participant in enumerate(participants):
        amount_owed = split_amount
        if i == 0:
            amount_owed += remainder
        splits.append((participant, amount_owed))

    return splits, None


def _splits_by_value(request, amount, participants):
    """Lê um valor exato por participante; a soma tem que bater com o total."""
    total_split_sum = Decimal('0.00')
    splits = []

    for participant in participants:
        raw_value = (request.POST.get(f'split_user_{participant.id}') or "0").strip()
        norm_value = raw_value.replace(".", "").replace(",", ".")

        try:
            value_decimal = Decimal(norm_value)
            if value_decimal < 0:
                raise InvalidOperation("Valor não pode ser negativo")
        except (InvalidOperation, AttributeError):
            return None, f"Valor inválido inserido para {participant.display_name}."

        total_split_sum += value_decimal
        if value_decimal > 0:
            splits.append((participant, value_decimal))

    if total_split_sum.quantize(Decimal("0.01")) != amount.quantize(Decimal("0.01")):
        return None, (
            f"A soma das partes (R$ {total_split_sum:.2f}) não corresponde "
            f"ao valor total da despesa (R$ {amount:.2f})"
        )

    return splits, None


def _splits_by_percentage(request, amount, participants):
    """Lê uma porcentagem por participante; a soma tem que fechar 100%."""
    total_percentage_sum = Decimal('0.00')
    perc_data = []

    for participant in participants:
        raw_perc = (request.POST.get(f'split_perc_{participant.id}') or "0").strip()
        norm_perc = raw_perc.replace(",", ".")

        try:
            perc_decimal = Decimal(norm_perc)
            if perc_decimal < 0:
                raise InvalidOperation("Porcentagem não pode ser negativa")
        except (InvalidOperation, AttributeError):
            return None, f"Porcentagem inválida para {participant.display_name}."

        total_percentage_sum += perc_decimal
        perc_data.append((participant, perc_decimal))

    if total_percentage_sum.quantize(Decimal("0.01")) != Decimal('100.00'):
        return None, (
            f"A soma das porcentagens ({total_percentage_sum:.2f}%) não é "
            f"exatamente 100%"
        )

    perc_data.sort(key=lambda x: x[0].id)

    splits = []
    total_calculated_amount = Decimal('0.00')

    for participant, percentage in perc_data:
        amount_owed = (percentage / Decimal('100.00') * amount).quantize(
            Decimal("0.01"), rounding=decimal.ROUND_HALF_UP
        )
        total_calculated_amount += amount_owed

        if amount_owed > 0:
            splits.append((participant, amount_owed))

    remainder = amount - total_calculated_amount
    if remainder != Decimal('0.00') and splits:
        splits[0] = (splits[0][0], splits[0][1] + remainder)

    return splits, None


def _parse_expense_form(request, group, participants):
    """
    Valida o formulário de despesa e já calcula a divisão.

    Devolve ``(dados, None)`` quando está tudo certo, ou ``(None, "mensagem")``
    com o primeiro problema encontrado.
    """
    description = (request.POST.get("description") or "").strip()
    raw_amount = (request.POST.get("amount") or "").strip()
    paid_by_id = request.POST.get("paid_by")
    split_method = request.POST.get("split_method", "EQUAL")

    if not description:
        return None, "Informe a descrição da despesa."

    norm = raw_amount.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(norm)
    except (InvalidOperation, AttributeError):
        return None, "Informe um valor válido (ex.: 100,00)."

    if amount <= 0:
        return None, "O valor da despesa deve ser maior que zero"

    try:
        payer = group.participants.get(pk=paid_by_id)
    except (Participant.DoesNotExist, ValueError, TypeError):
        return None, "Selecione quem pagou."

    if split_method == 'EQUAL':
        splits, error = _splits_equal(amount, participants)
    elif split_method == 'UNEQUAL_VALUE':
        splits, error = _splits_by_value(request, amount, participants)
    elif split_method == 'UNEQUAL_PERCENTAGE':
        splits, error = _splits_by_percentage(request, amount, participants)
    else:
        return None, "Método de divisão inválido."

    if error:
        return None, error

    return {
        "description": description,
        "amount": amount,
        "payer": payer,
        "split_method": split_method,
        "splits": splits,
    }, None


def _save_expense(group, data, expense=None):
    """Cria ou atualiza a despesa junto com as divisões dela, tudo ou nada."""
    with transaction.atomic():
        if expense is None:
            expense = Expense.objects.create(
                group=group,
                description=data["description"],
                amount=data["amount"],
                paid_by=data["payer"],
                split_method=data["split_method"],
            )
        else:
            expense.description = data["description"]
            expense.amount = data["amount"]
            expense.paid_by = data["payer"]
            expense.split_method = data["split_method"]
            expense.save()
            expense.splits.all().delete()

        ExpenseSplit.objects.bulk_create([
            ExpenseSplit(expense=expense, participant=participant, amount_owed=amount_owed)
            for participant, amount_owed in data["splits"]
        ])

    return expense


def _expense_form_context(group, participants, request, expense=None):
    """
    Contexto do formulário de despesa, servindo tanto para criar quanto para editar.

    Os valores por participante são pendurados no próprio participante
    (``split_value``/``split_perc``) porque o template não consegue montar a
    chave ``split_user_<id>`` dinamicamente.
    """
    submitted = request.POST if request.method == "POST" else {}

    if submitted:
        initial = {
            "description": submitted.get("description", ""),
            "amount": submitted.get("amount", ""),
            "paid_by": submitted.get("paid_by", ""),
            "split_method": submitted.get("split_method", "EQUAL"),
        }
    elif expense is not None:
        initial = {
            "description": expense.description,
            "amount": f"{expense.amount:.2f}".replace(".", ","),
            "paid_by": str(expense.paid_by_id),
            "split_method": expense.split_method,
        }
    else:
        initial = {"description": "", "amount": "", "paid_by": "", "split_method": "EQUAL"}

    saved_splits = {}
    if expense is not None:
        saved_splits = {s.participant_id: s.amount_owed for s in expense.splits.all()}

    for participant in participants:
        if submitted:
            participant.split_value = submitted.get(f"split_user_{participant.id}", "")
            participant.split_perc = submitted.get(f"split_perc_{participant.id}", "")
            continue

        owed = saved_splits.get(participant.id)
        participant.split_value = "" if owed is None else f"{owed:.2f}".replace(".", ",")

        if owed is None or not expense or not expense.amount:
            participant.split_perc = ""
        else:
            perc = (owed / expense.amount * Decimal("100")).quantize(
                Decimal("0.1"), rounding=decimal.ROUND_HALF_UP
            )
            participant.split_perc = f"{perc}"

    return {
        "group": group,
        "participants": participants,
        "expense": expense,
        "initial": initial,
    }


@login_required
def add_expense(request, group_id):
    group = get_object_or_404(Group, pk=group_id)

    if not _can_manage(group, request.user) and not Participant.objects.filter(group=group, user=request.user).exists():
        messages.error(request, "Você não participa deste grupo.")
        return redirect("rachais:group_detail", group_id=group.id)

    participants = list(group.participants.select_related("user").all())

    if request.method == "POST":
        data, error = _parse_expense_form(request, group, participants)

        if error:
            messages.error(request, error)
            return render(request, "rachais/add_expense.html", _expense_form_context(group, participants, request))

        try:
            _save_expense(group, data)
        except Exception as e:
            messages.error(request, f"Erro ao salvar: {e}")
            return render(request, "rachais/add_expense.html", _expense_form_context(group, participants, request))

        messages.success(request, "Despesa registrada com sucesso.")
        return redirect("rachais:group_detail", group_id=group.id)

    return render(request, "rachais/add_expense.html", _expense_form_context(group, participants, request))


@login_required
def edit_expense(request, group_id, expense_id):
    """Edita uma despesa já registrada, recalculando a divisão do zero."""
    group = get_object_or_404(_sidebar_groups_qs(request.user), pk=group_id)
    expense = get_object_or_404(group.expenses.select_related("paid_by"), pk=expense_id)

    if not _can_edit_expense(group, expense, request.user):
        messages.error(request, "Você não pode editar esta despesa.")
        return redirect("rachais:group_detail", group_id=group.id)

    participants = list(group.participants.select_related("user").all())

    if request.method == "POST":
        data, error = _parse_expense_form(request, group, participants)

        if error:
            messages.error(request, error)
            return render(request, "rachais/add_expense.html", _expense_form_context(group, participants, request, expense))

        try:
            with transaction.atomic():
                _save_expense(group, data, expense=expense)
                removed = _prune_orphan_payments(group)
        except Exception as e:
            messages.error(request, f"Erro ao salvar: {e}")
            return render(request, "rachais/add_expense.html", _expense_form_context(group, participants, request, expense))

        messages.success(request, "Despesa atualizada com sucesso.")
        _warn_about_pruned_payments(request, removed)
        return redirect("rachais:group_detail", group_id=group.id)

    return render(request, "rachais/add_expense.html", _expense_form_context(group, participants, request, expense))


@require_POST
@login_required
def delete_expense(request, group_id, expense_id):
    """Apaga uma despesa e, junto, todas as divisões dela."""
    group = get_object_or_404(_sidebar_groups_qs(request.user), pk=group_id)
    expense = get_object_or_404(group.expenses.select_related("paid_by"), pk=expense_id)

    if not _can_edit_expense(group, expense, request.user):
        messages.error(request, "Você não pode apagar esta despesa.")
        return redirect("rachais:group_detail", group_id=group.id)

    description = expense.description
    with transaction.atomic():
        expense.delete()
        removed = _prune_orphan_payments(group)

    messages.success(request, f'Despesa "{description}" apagada.')
    _warn_about_pruned_payments(request, removed)
    return redirect("rachais:group_detail", group_id=group.id)


@require_POST
@login_required
def pay_debt(request):
    """
    Registra a quitação de uma dívida.

    Um participante com conta quita a própria dívida. O criador do grupo pode,
    além disso, registrar acertos de terceiros — necessário para participantes
    avulsos, que não têm login para fazer isso sozinhos.
    """
    group_id = request.POST.get("group_id")
    payer_id = request.POST.get("payer_id")
    receiver_id = request.POST.get("receiver_id")
    amount_raw = request.POST.get("amount")

    if not (group_id and receiver_id and amount_raw):
        messages.error(request, "Requisição inválida.")
        return redirect("rachais:group_list")

    group = get_object_or_404(Group, pk=group_id)
    is_manager = _can_manage(group, request.user)
    my_participant = _my_participant(group, request.user)

    if not is_manager and not my_participant:
        messages.error(request, "Você não participa deste grupo.")
        return redirect("rachais:group_detail", group_id=group.id)

    if payer_id and is_manager:
        payer = group.participants.filter(pk=payer_id).first()
        if not payer:
            messages.error(request, "Participante não encontrado neste grupo.")
            return redirect("rachais:group_detail", group_id=group.id)
    elif payer_id and str(getattr(my_participant, "id", "")) != str(payer_id):
        messages.error(request, "Você só pode registrar as suas próprias dívidas.")
        return redirect("rachais:group_detail", group_id=group.id)
    else:
        payer = my_participant

    if not payer:
        messages.error(request, "Você não participa deste grupo.")
        return redirect("rachais:group_detail", group_id=group.id)

    amount_raw = amount_raw.replace(',', '.').strip()

    try:
        amount = Decimal(amount_raw)
    except (InvalidOperation, TypeError):
        messages.error(request, "Valor inválido.")
        return redirect("rachais:group_detail", group_id=group.id)

    participants = list(group.participants.select_related("user"))
    balances = _calculate_balances(group)
    settlements = _calculate_settlements(balances, participants)

    candidate = next(
        (
            s for s in settlements
            if s.person_from.id == payer.id
            and str(s.person_to.id) == str(receiver_id)
        ),
        None,
    )

    if candidate is None:
        messages.error(request, "Não encontramos essa dívida ou ela já foi quitada.")
        return redirect("rachais:group_detail", group_id=group.id)

    transfer_amount = candidate.amount.quantize(Decimal("0.01"))
    if amount.quantize(Decimal("0.01")) != transfer_amount:
        messages.error(request, "O valor informado não corresponde ao saldo atual dessa dívida.")
        return redirect("rachais:group_detail", group_id=group.id)

    Payment.objects.create(
        group=group,
        payer=payer,
        receiver_id=candidate.person_to.id,
        amount=transfer_amount,
        created_by=request.user,
    )
    messages.success(request, "Pagamento registrado com sucesso!")
    return redirect("rachais:group_detail", group_id=group.id)


def _can_undo_payment(group, payment, user):
    """
    Quem pode desfazer um acerto já registrado.

    O criador do grupo sempre pode — é ele quem registra os acertos de
    participantes avulsos, que não têm login para se corrigir. Fora isso,
    desfaz quem pagou.
    """
    if _can_manage(group, user):
        return True
    return payment.payer.user_id == user.id


@require_POST
@login_required
def undo_payment(request, group_id, payment_id):
    """Remove um pagamento registrado por engano, devolvendo a dívida."""
    group = get_object_or_404(_sidebar_groups_qs(request.user), pk=group_id)
    payment = get_object_or_404(
        group.payments.select_related("payer__user", "receiver__user"), pk=payment_id
    )

    if not _can_undo_payment(group, payment, request.user):
        messages.error(request, "Você não pode desfazer este pagamento.")
        return redirect("rachais:group_detail", group_id=group.id)

    payer_name = payment.payer.display_name
    receiver_name = payment.receiver.display_name

    with transaction.atomic():
        payment.delete()
        # tirar um acerto pode deixar outros sem lastro
        removed = _prune_orphan_payments(group)

    messages.success(
        request,
        f"Pagamento de {payer_name} para {receiver_name} desfeito.",
    )
    _warn_about_pruned_payments(request, removed)
    return redirect("rachais:group_detail", group_id=group.id)
