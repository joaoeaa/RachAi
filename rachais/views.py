from decimal import Decimal, InvalidOperation
import decimal
from collections import defaultdict
from types import SimpleNamespace
from django.db import transaction
from .auth_views import login_guest
from .templatetags.rachai import brl
from .models import (
    Group, Participant, Expense, ExpensePayer, ExpenseSplit, Payment,
    create_guest_user, user_display_name,
)
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_POST

User = get_user_model()


def _sidebar_groups_qs(user):
    """QS padrão para preencher a sidebar com os grupos do usuário."""
    return (
        Group.objects
        .filter(
            Q(participants__user=user, participants__left_at__isnull=True)
            | Q(creator=user)
        )
        .distinct()
        .order_by("-created_at")
        .prefetch_related("participants__user")
    )


def _my_participant(group, user):
    """O Participant que representa `user` dentro de `group`, se houver."""
    return group.participants.filter(user=user, left_at__isnull=True).first()


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

    # Despesa paga por várias pessoas tem as linhas em ExpensePayer, que valem
    # no lugar do paid_by; as demais seguem com o paid_by pagando tudo.
    com_varios = set()
    pagadores = ExpensePayer.objects.filter(expense__group_id__in=group_ids).values_list(
        'expense__group_id', 'expense_id', 'participant_id', 'amount_paid'
    )
    for group_id, expense_id, participant_id, amount_paid in pagadores:
        balances[group_id][participant_id] += amount_paid
        com_varios.add(expense_id)

    despesas = Expense.objects.filter(group_id__in=group_ids).values_list(
        'id', 'group_id', 'paid_by_id', 'amount'
    )
    for expense_id, group_id, payer_id, amount in despesas:
        if expense_id not in com_varios:
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
    ).select_related('user__profile').order_by('id')

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

    # Sem ordenar, o pareamento saía na ordem em que o banco devolveu as linhas:
    # a mesma dívida podia aparecer com pessoas diferentes a cada carregamento.
    # Maior credor com maior devedor também reduz o número de transferências.
    creditors.sort(key=lambda item: (-item[1], item[0]))
    debtors.sort(key=lambda item: (-item[1], item[0]))

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
        "groups": sidebar_groups,
    }


@login_required
def group_list(request):
    """Home: saldo geral e um cartão por grupo, tudo a partir do snapshot em lote."""
    snap = _my_debts_snapshot(request.user)
    cards = []
    for g in snap["groups"]:
        parts = snap["participants_by_group"].get(g.id, [])
        mine = next((p for p in parts if p.user_id == request.user.id), None)
        balance = Decimal("0")
        if mine:
            balance = snap["balances_by_group"].get(g.id, {}).get(mine.id, Decimal("0"))
        if abs(balance) < Decimal("0.01"):
            balance = Decimal("0")
        members = sum(1 for p in parts if p.left_at is None)
        cards.append(SimpleNamespace(group=g, members=members, balance=balance))
    total_pay = sum((d["amount"] for d in snap["pending_to_pay"]), Decimal("0"))
    total_receive = sum((d["amount"] for d in snap["pending_to_receive"]), Decimal("0"))
    return render(request, "rachais/group_list.html", {
        "groups": snap["groups"],
        "cards": cards,
        "total_pay": total_pay,
        "total_receive": total_receive,
        "net": total_receive - total_pay,
        "all_settled": not total_pay and not total_receive,
    })


@login_required
def group_detail(request, group_id):
    groups = _sidebar_groups_qs(request.user)
    group = get_object_or_404(groups, pk=group_id)

    expenses = list(group.expenses.select_related("paid_by__user").all())

    total = sum((e.amount for e in expenses), Decimal("0"))

    # O snapshot da sidebar já calcula saldos e participantes de todos os grupos
    # em lote — reaproveitamos os deste grupo em vez de consultar de novo.
    my_debts = _my_debts_snapshot(request.user)
    all_participants = my_debts["participants_by_group"].get(group.id) or list(
        group.participants.select_related("user__profile")
    )
    balances = my_debts["balances_by_group"].get(group.id, {})

    board = _group_board(all_participants, balances, expenses)
    participants = board.participants
    all_splits = board.all_splits

    for e in expenses:
        setattr(e, "can_edit", _can_edit_expense(group, e, request.user))

    # Painel do dono: quem dá para remover agora e, se não der, por quê.
    for p in participants:
        p.removable = False
        p.manage_warn = False
        if p.user_id == group.creator_id:
            p.manage_note = "Dono do grupo"
        elif p.balance != 0:
            p.manage_warn = True
            situacao = "a receber" if p.balance > 0 else "a pagar"
            p.manage_note = f"Tem {brl(p.balance)} {situacao} · acerte antes de remover"
        else:
            p.removable = True
            p.manage_note = "Em dia · pode ser removido(a)"

    me = next((p for p in participants if p.user_id == request.user.id), None)
    my_balance = me.balance if me else Decimal("0")
    my_share = sum(
        (s.amount_owed for s in all_splits if me and s.participant_id == me.id),
        Decimal("0"),
    )
    # A página do grupo mostra só as pendências deste grupo.
    group_debts = {
        key: [d for d in my_debts[key] if d["group"].id == group.id]
        for key in ("pending_to_pay", "pending_to_receive", "managed_pending")
    }
    group_debts["paid"] = [pay for pay in my_debts["paid"] if pay.group_id == group.id]
    pending_count = sum(len(group_debts[k]) for k in ("pending_to_pay", "pending_to_receive", "managed_pending"))

    is_manager = _can_manage(group, request.user)
    invite_url = None
    share_url = None
    if is_manager and group.is_managed:
        share_url = request.build_absolute_uri(
            reverse("rachais:shared_group", args=[group.ensure_share_token()])
        )
    elif is_manager:
        invite_url = request.build_absolute_uri(
            reverse("rachais:join_group", args=[group.ensure_invite_token()])
        )

    return render(
        request,
        "rachais/group_detail.html",
        {
            "group": group,
            "groups": groups,
            "expenses": expenses,
            "participants": participants,
            "balance_rows": board.balance_rows,
            "total": total,
            "settlements": board.settlements,
            "my_debts": group_debts,
            "pending_count": pending_count,
            "my_balance": my_balance,
            "my_share": my_share,
            "my_settled": my_balance == 0,
            "can_manage": is_manager,
            "invite_url": invite_url,
            "share_url": share_url,
        }
    )


def _group_board(all_participants, balances, expenses):
    """
    O quadro do grupo que todo mundo vê igual: saldos, quem paga quem e como
    cada despesa foi dividida. Usado na página do grupo e no link só para ver.
    """
    # As contas usam todo mundo que já passou pelo grupo; a tela mostra só quem
    # continua nele (removidos já saíram com o saldo zerado).
    settlements = _calculate_settlements(balances, all_participants)

    max_abs = max((abs(balances.get(p.id, Decimal("0"))) for p in all_participants), default=Decimal("0"))
    for participant in all_participants:
        balance = balances.get(participant.id, Decimal("0"))
        if abs(balance) < Decimal("0.01"):
            balance = Decimal("0")
        setattr(participant, "balance", balance)
        setattr(participant, "balance_abs", balance.copy_abs())
        setattr(participant, "bar", int(round(abs(balance) / max_abs * 100)) if max_abs else 0)
        amount_txt = brl(balance)
        if balance > 0:
            sub = f"Tem {amount_txt} a receber no grupo"
        elif balance < 0:
            sub = f"Deve {amount_txt} no grupo"
        else:
            sub = "Está em dia no grupo"
        setattr(participant, "pix_sub", sub)

    participants = [p for p in all_participants if p.left_at is None]
    # Removido com saldo só acontece se alguém editar uma despesa antiga depois;
    # nesse caso a pessoa volta a aparecer nos saldos para a conta fechar.
    visible = [p for p in all_participants if p.left_at is None or p.balance != 0]

    # Saldos agrupados: primeiro quem tem a receber (do maior para o menor),
    # depois quem tem a pagar (da maior dívida para a menor) e, por fim, os quites.
    balance_rows = (
        sorted((p for p in visible if p.balance > 0), key=lambda p: -p.balance)
        + sorted((p for p in visible if p.balance < 0), key=lambda p: p.balance)
        + [p for p in visible if p.balance == 0]
    )

    all_splits = ExpenseSplit.objects.filter(
        expense__in=expenses
    ).select_related('participant__user').order_by('id')

    splits_by_expense = defaultdict(list)
    for split in all_splits:
        splits_by_expense[split.expense_id].append(split)

    payers_by_expense = defaultdict(list)
    for row in ExpensePayer.objects.filter(
        expense__in=expenses
    ).select_related('participant__user').order_by('id'):
        payers_by_expense[row.expense_id].append(row)

    member_ids = {p.id for p in participants}
    for e in expenses:
        e.payer_list = payers_by_expense.get(e.id, [])
        e.payer_text = _payer_summary(e, e.payer_list)
        setattr(e, "split_text", _split_summary(e, splits_by_expense.get(e.id, []), member_ids))

    return SimpleNamespace(
        participants=participants,
        balance_rows=balance_rows,
        settlements=settlements,
        all_splits=list(all_splits),
    )


def shared_group(request, token):
    """
    Link "só para ver" de um grupo gerenciado: abre sem login e sem nenhum botão
    de alteração. Quem tem o link vê saldos, quem paga quem, despesas e acertos.
    """
    group = Group.objects.select_related("creator").filter(share_token=token).first()

    if group is None:
        response = render(request, "rachais/shared_group.html", {"invalid": True}, status=404)
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response

    expenses = list(group.expenses.select_related("paid_by__user"))
    all_participants = list(group.participants.select_related("user__profile").order_by("id"))
    board = _group_board(all_participants, _calculate_balances(group), expenses)
    payments = list(group.payments.select_related("payer__user", "receiver__user"))

    response = render(request, "rachais/shared_group.html", {
        "group": group,
        "creator_name": user_display_name(group.creator),
        "participants": board.participants,
        "balance_rows": board.balance_rows,
        "settlements": board.settlements,
        "expenses": expenses,
        "payments": payments,
        "total": sum((e.amount for e in expenses), Decimal("0")),
    })
    # Link privado: não deve parar em buscador.
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@login_required
@require_POST
def regenerate_share(request, group_id):
    """Gera um novo link só para ver; o anterior deixa de abrir."""
    group = get_object_or_404(Group, pk=group_id)

    if not _can_manage(group, request.user) or not group.is_managed:
        messages.error(request, "Apenas o dono de um grupo gerenciado pode gerenciar esse link.")
        return redirect("rachais:group_detail", group_id=group.id)

    group.regenerate_share_token()
    messages.success(request, "Novo link gerado. O link antigo não abre mais.")
    return redirect("rachais:group_detail", group_id=group.id)


def _join_names(names):
    """['Ana', 'Bia', 'Caio'] -> 'Ana, Bia e Caio'."""
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " e " + names[-1]


def _payer_summary(expense, payer_rows):
    """Quem pagou a despesa: 'Ana pagou', 'Ana e Bia pagaram · R$ 50,00 cada'..."""
    if not payer_rows:
        return f"{expense.paid_by.display_name} pagou"
    if expense.pay_split_method == "EQUAL":
        names = _join_names(row.participant.display_name for row in payer_rows)
        share = brl((expense.amount / len(payer_rows)).quantize(Decimal("0.01")))
        text = f"{names} pagaram · {share} cada"
    else:
        text = "Pagaram: " + " · ".join(
            f"{row.participant.display_name} {brl(row.amount_paid)}" for row in payer_rows
        )
    return text.replace("R$ ", "R$ ")


def _split_summary(expense, splits, member_ids):
    """Uma linha curta dizendo como a despesa foi dividida."""
    # espaço fixo depois do R$: no celular o valor não quebra no meio
    return _split_summary_text(expense, splits, member_ids).replace("R$ ", "R$\u00a0")


def _split_summary_text(expense, splits, member_ids):
    if not splits:
        return f"{expense.get_split_method_display()} · ninguém para dividir ainda"

    if expense.split_method == "EQUAL":
        if len(splits) == 1:
            return f"Fica tudo com {splits[0].participant.display_name}"
        share = brl((expense.amount / len(splits)).quantize(Decimal("0.01")))
        ids = {s.participant_id for s in splits}
        if len(member_ids) > 1 and ids == member_ids:
            return f"Dividido igualmente entre todos ({len(splits)}) · {share} cada"
        names = _join_names(s.participant.display_name for s in splits)
        return f"Dividido igualmente entre {names} · {share} cada"

    # Com um pagador só, a parte dele fica implícita; com vários, mostra todo mundo.
    several_payers = bool(getattr(expense, "payer_list", None))
    parts = [
        f"{s.participant.display_name} {brl(s.amount_owed)}"
        for s in splits
        if several_payers or s.participant_id != expense.paid_by_id
    ]
    if not parts:
        return f"{expense.get_split_method_display()} · fica tudo com quem pagou"
    return f"{expense.get_split_method_display()}: " + " · ".join(parts)


@login_required
@require_POST
def delete_group(request, group_id):
    """Apaga o grupo inteiro (despesas, acertos e participantes). Só o dono pode."""
    group = get_object_or_404(_sidebar_groups_qs(request.user), pk=group_id)

    if group.creator_id != request.user.id:
        messages.error(request, "Apenas o dono do grupo pode apagá-lo.")
        return redirect("rachais:group_detail", group_id=group.id)

    name = group.name
    group.delete()
    messages.success(request, f'Grupo "{name}" apagado.')
    return redirect("rachais:group_list")


def _has_history(participant):
    """Se a pessoa aparece em alguma despesa ou acerto do grupo."""
    return (
        Expense.objects.filter(paid_by=participant).exists()
        or ExpensePayer.objects.filter(participant=participant).exists()
        or ExpenseSplit.objects.filter(participant=participant).exists()
        or Payment.objects.filter(Q(payer=participant) | Q(receiver=participant)).exists()
    )


@login_required
@require_POST
def remove_participant(request, group_id, participant_id):
    """
    Tira alguém do grupo. Só o dono pode, e só de quem está com o saldo zerado.

    Quem nunca entrou em nenhuma conta é apagado de vez. Quem já tem histórico
    é marcado como removido: as despesas antigas continuam fechando, mas a
    pessoa some da lista, não entra em despesas novas e perde o acesso.
    """
    group = get_object_or_404(_sidebar_groups_qs(request.user), pk=group_id)

    if not _can_manage(group, request.user):
        messages.error(request, "Apenas o dono do grupo pode remover participantes.")
        return redirect("rachais:group_detail", group_id=group.id)

    participant = get_object_or_404(
        group.participants.select_related("user"), pk=participant_id, left_at__isnull=True
    )
    name = participant.display_name

    if participant.user_id == group.creator_id:
        messages.error(request, "O dono do grupo não pode ser removido.")
        return redirect("rachais:group_detail", group_id=group.id)

    balance = _calculate_balances(group).get(participant.id, Decimal("0"))
    if abs(balance) >= Decimal("0.01"):
        situacao = "a receber" if balance > 0 else "a pagar"
        messages.error(
            request,
            f"{name} ainda tem {brl(balance)} {situacao} no grupo. "
            "Registre os acertos antes de remover.",
        )
        return redirect("rachais:group_detail", group_id=group.id)

    with transaction.atomic():
        if _has_history(participant):
            participant.left_at = timezone.now()
            participant.save(update_fields=["left_at"])
        else:
            participant.delete()

    messages.success(request, f"{name} saiu do grupo.")
    return redirect("rachais:group_detail", group_id=group.id)

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

        existing = group.participants.filter(guest_name__iexact=guest_name).first()
        if existing and existing.left_at is None:
            messages.info(request, "Já existe um participante com esse nome no grupo.")
            return redirect("rachais:group_detail", group_id=group.id)

        if existing:
            existing.left_at = None
            existing.save(update_fields=["left_at"])
            messages.success(request, f"{existing.display_name} voltou para o grupo!")
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

    existing = Participant.objects.filter(group=group, user=user).first()
    if existing and existing.left_at is None:
        messages.info(request, "Esse usuário já está no grupo.")
        return redirect("rachais:group_detail", group_id=group.id)

    if existing:
        existing.left_at = None
        existing.save(update_fields=["left_at"])
    else:
        Participant.objects.create(group=group, user=user)
    messages.success(request, f"{user_display_name(user)} adicionado(a) com sucesso!")
    return redirect("rachais:group_detail", group_id=group.id)


GUEST_NAME_MAX_LENGTH = 60


def _guest_name_error(group, name):
    """Problema com o nome escolhido por quem entra sem conta, ou ``None``."""
    if not name:
        return "Escolha um nome para entrar no grupo."
    if len(name) > GUEST_NAME_MAX_LENGTH:
        return f"O nome deve ter no máximo {GUEST_NAME_MAX_LENGTH} caracteres."
    taken = {
        p.display_name.casefold()
        for p in group.participants.filter(left_at__isnull=True).select_related("user")
    }
    if name.casefold() in taken:
        return "Já tem alguém com esse nome no grupo. Que tal colocar o sobrenome ou um apelido?"
    return None


def join_group(request, token):
    """
    Entrada pelo link de convite de um grupo colaborativo.

    Quem está logado só confirma. Quem não está escolhe: entrar só com um nome
    (vira um convidado, com conta sem e-mail nem senha) ou entrar/criar conta, que
    passa pelo login/cadastro e volta para cá pelo ``next``. O GET só mostra o
    convite; entrar no grupo é sempre um POST.
    """
    group = (
        Group.objects
        .select_related("creator")
        .filter(invite_token=token, mode=Group.MODE_SHARED)
        .first()
    )

    if group is None:
        return render(request, "rachais/join_group.html", {"invalid": True}, status=404)

    if request.user.is_authenticated and group.participants.filter(
        user=request.user, left_at__isnull=True
    ).exists():
        messages.info(request, "Você já faz parte deste grupo.")
        return redirect("rachais:group_detail", group_id=group.id)

    guest_name = ""
    if request.method == "POST":
        if request.user.is_authenticated:
            participant, _ = Participant.objects.get_or_create(group=group, user=request.user)
            if participant.left_at is not None:
                participant.left_at = None
                participant.save(update_fields=["left_at"])
            messages.success(request, f"Você entrou no grupo {group.name}!")
            return redirect("rachais:group_detail", group_id=group.id)

        guest_name = " ".join((request.POST.get("guest_name") or "").split())
        error = _guest_name_error(group, guest_name)
        if error:
            messages.error(request, error)
        else:
            with transaction.atomic():
                user = create_guest_user(guest_name)
                Participant.objects.create(group=group, user=user)
            login_guest(request, user)
            messages.success(
                request,
                f"Você entrou no grupo {group.name}! Para não perder o acesso, "
                "guarde seu link pessoal ou complete o cadastro em Minha conta.",
            )
            return redirect("rachais:group_detail", group_id=group.id)

    return render(
        request,
        "rachais/join_group.html",
        {
            "group": group,
            "creator_name": user_display_name(group.creator),
            "participant_count": group.participants.filter(left_at__isnull=True).count(),
            "preview": list(
                group.participants.filter(left_at__isnull=True)
                .select_related("user").order_by("id")[:4]
            ),
            "guest_name": guest_name,
            "guest_name_max": GUEST_NAME_MAX_LENGTH,
        },
    )


@login_required
@require_POST
def regenerate_invite(request, group_id):
    """Gera um novo link de convite; o anterior deixa de funcionar."""
    group = get_object_or_404(Group, pk=group_id)

    if not _can_manage(group, request.user) or group.is_managed:
        messages.error(request, "Apenas o criador de um grupo colaborativo pode gerenciar o convite.")
        return redirect("rachais:group_detail", group_id=group.id)

    group.regenerate_invite_token()
    messages.success(request, "Novo link de convite gerado. O link antigo não funciona mais.")
    return redirect("rachais:group_detail", group_id=group.id)

def _can_edit_expense(group, expense, user):
    """
    Quem pode mexer numa despesa já lançada.

    O criador do grupo sempre pode — inclusive nas despesas de participantes
    avulsos, que não têm login. Fora isso, cada um corrige o que pagou (também
    quando dividiu o pagamento com outras pessoas).
    """
    if _can_manage(group, user):
        return True
    if expense.paid_by.user_id == user.id:
        return True
    payer_list = getattr(expense, "payer_list", None)
    if payer_list is not None:
        return any(row.participant.user_id == user.id for row in payer_list)
    return expense.payers.filter(participant__user=user).exists()


def _expense_only_balances(group):
    """Saldos considerando apenas as despesas, sem os pagamentos já registrados."""
    balances = defaultdict(Decimal)
    expenses = list(group.expenses.all())

    com_varios = set()
    for row in ExpensePayer.objects.filter(expense__in=expenses):
        balances[row.participant_id] += row.amount_paid
        com_varios.add(row.expense_id)

    for expense in expenses:
        if expense.id not in com_varios:
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


def _splits_equal(amount, participants, empty_error="Escolha pelo menos uma pessoa para dividir a despesa."):
    """Divide o valor em partes iguais; a sobra dos centavos fica com o primeiro."""
    if not participants:
        return None, empty_error

    split_amount = round(amount / len(participants), 2)
    remainder = amount - (split_amount * len(participants))

    splits = []
    for i, participant in enumerate(participants):
        amount_owed = split_amount
        if i == 0:
            amount_owed += remainder
        splits.append((participant, amount_owed))

    return splits, None


def _splits_by_value(data, amount, participants, prefix="split_user_", what="das partes"):
    """Lê um valor exato por participante; a soma tem que bater com o total."""
    total_split_sum = Decimal('0.00')
    splits = []

    for participant in participants:
        raw_value = (data.get(f'{prefix}{participant.id}') or "0").strip()
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
            f"A soma {what} (R$ {total_split_sum:.2f}) não corresponde "
            f"ao valor total da despesa (R$ {amount:.2f})"
        )

    return splits, None


def _splits_by_percentage(data, amount, participants, prefix="split_perc_", what="das porcentagens"):
    """Lê uma porcentagem por participante; a soma tem que fechar 100%."""
    total_percentage_sum = Decimal('0.00')
    perc_data = []

    for participant in participants:
        raw_perc = (data.get(f'{prefix}{participant.id}') or "0").strip()
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
            f"A soma {what} ({total_percentage_sum:.2f}%) não é "
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


def _equal_split_people(data, participants, marker="split_select", field="split_include"):
    """
    Quem entra numa divisão igual. O formulário manda o ``marker`` junto com as
    caixinhas marcadas; sem ele (cliente antigo), entram todos.
    """
    if not data.get(marker):
        return participants
    chosen = set(data.getlist(field))
    return [p for p in participants if str(p.id) in chosen]


# Os dois quadros do formulário de despesa usam a mesma mecânica: o de quem
# consumiu (divisão) e o de quem pagou (só quando mais de uma pessoa pagou).
SPLIT_FIELDS = {
    "method": "split_method", "marker": "split_select", "include": "split_include",
    "value": "split_user_", "perc": "split_perc_",
}
PAY_FIELDS = {
    "method": "pay_method", "marker": "payer_select", "include": "payer_include",
    "value": "pay_user_", "perc": "pay_perc_",
}


def _read_shares(data, amount, participants, fields, method, *, paying=False):
    """Lê um dos quadros (divisão ou pagamento) e devolve ``(partes, erro)``."""
    if method == "EQUAL":
        people = _equal_split_people(data, participants, fields["marker"], fields["include"])
        if paying:
            return _splits_equal(amount, people, "Marque quem pagou a despesa.")
        return _splits_equal(amount, people)
    if method == "UNEQUAL_VALUE":
        return _splits_by_value(
            data, amount, participants, fields["value"],
            "do que cada um pagou" if paying else "das partes",
        )
    if method == "UNEQUAL_PERCENTAGE":
        return _splits_by_percentage(
            data, amount, participants, fields["perc"],
            "das porcentagens pagas" if paying else "das porcentagens",
        )
    return None, "Método de divisão inválido."


def _parse_expense_form(request, group, participants):
    """
    Valida o formulário de despesa e já calcula a divisão.

    Devolve ``(dados, None)`` quando está tudo certo, ou ``(None, "mensagem")``
    com o primeiro problema encontrado.
    """
    data = request.POST
    description = (data.get("description") or "").strip()
    raw_amount = (data.get("amount") or "").strip()
    split_method = data.get("split_method", "EQUAL")

    if not description:
        return None, "Informe a descrição da despesa."

    norm = raw_amount.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(norm)
    except (InvalidOperation, AttributeError):
        return None, "Informe um valor válido (ex.: 100,00)."

    if amount <= 0:
        return None, "O valor da despesa deve ser maior que zero"

    pay_method = "EQUAL"
    payers = []
    if data.get("multi_payer"):
        pay_method = data.get("pay_method", "EQUAL")
        payers, error = _read_shares(data, amount, participants, PAY_FIELDS, pay_method, paying=True)
        if error:
            return None, error
        if not payers:
            return None, "Marque quem pagou a despesa."
        # Quem pagou mais aparece como pagador principal (avatar, permissões).
        payer = max(payers, key=lambda item: item[1])[0]
        if len(payers) == 1:
            payers = []
    else:
        paid_by_id = data.get("paid_by")
        payer = next((p for p in participants if str(p.id) == str(paid_by_id)), None)
        if payer is None:
            return None, "Selecione quem pagou."

    splits, error = _read_shares(data, amount, participants, SPLIT_FIELDS, split_method)
    if error:
        return None, error

    return {
        "description": description,
        "amount": amount,
        "payer": payer,
        "payers": payers,
        "pay_method": pay_method if payers else "EQUAL",
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
                pay_split_method=data["pay_method"],
            )
        else:
            expense.description = data["description"]
            expense.amount = data["amount"]
            expense.paid_by = data["payer"]
            expense.split_method = data["split_method"]
            expense.pay_split_method = data["pay_method"]
            expense.save()
            expense.splits.all().delete()
            expense.payers.all().delete()

        ExpenseSplit.objects.bulk_create([
            ExpenseSplit(expense=expense, participant=participant, amount_owed=amount_owed)
            for participant, amount_owed in data["splits"]
        ])
        ExpensePayer.objects.bulk_create([
            ExpensePayer(expense=expense, participant=participant, amount_paid=amount_paid)
            for participant, amount_paid in data["payers"]
        ])

    return expense


def _money_input(value):
    return "" if value is None else f"{value:.2f}".replace(".", ",")


def _perc_input(value, total):
    if value is None or not total:
        return ""
    return f"{(value / total * Decimal('100')).quantize(Decimal('0.1'), rounding=decimal.ROUND_HALF_UP)}"


def _expense_form_context(group, participants, request, expense=None):
    """
    Contexto do formulário de despesa, servindo tanto para criar quanto para editar.

    Os valores por participante são pendurados no próprio participante
    (``split_value``/``pay_value``...) porque o template não consegue montar a
    chave ``split_user_<id>`` dinamicamente.
    """
    submitted = request.POST if request.method == "POST" else {}

    saved_splits = {}
    saved_payers = {}
    if expense is not None:
        saved_splits = {s.participant_id: s.amount_owed for s in expense.splits.all()}
        saved_payers = {p.participant_id: p.amount_paid for p in expense.payers.all()}

    if submitted:
        initial = {
            "description": submitted.get("description", ""),
            "amount": submitted.get("amount", ""),
            "paid_by": submitted.get("paid_by", ""),
            "split_method": submitted.get("split_method", "EQUAL"),
            "multi_payer": bool(submitted.get("multi_payer")),
            "pay_method": submitted.get("pay_method", "EQUAL"),
        }
    elif expense is not None:
        initial = {
            "description": expense.description,
            "amount": f"{expense.amount:.2f}".replace(".", ","),
            "paid_by": str(expense.paid_by_id),
            "split_method": expense.split_method,
            "multi_payer": bool(saved_payers),
            "pay_method": expense.pay_split_method if saved_payers else "EQUAL",
        }
    else:
        initial = {
            "description": "", "amount": "", "paid_by": "", "split_method": "EQUAL",
            "multi_payer": False, "pay_method": "EQUAL",
        }

    if submitted:
        included = {p.id for p in _equal_split_people(submitted, participants)}
        paying = {
            p.id for p in _equal_split_people(submitted, participants, "payer_select", "payer_include")
        }
    else:
        if expense is not None and expense.split_method == "EQUAL":
            included = set(saved_splits)
        else:
            included = {p.id for p in participants}
        if saved_payers:
            paying = set(saved_payers)
        elif expense is not None:
            paying = {expense.paid_by_id}
        else:
            paying = set()

    for participant in participants:
        participant.split_in = participant.id in included
        participant.pay_in = participant.id in paying
        if submitted:
            participant.split_value = submitted.get(f"split_user_{participant.id}", "")
            participant.split_perc = submitted.get(f"split_perc_{participant.id}", "")
            participant.pay_value = submitted.get(f"pay_user_{participant.id}", "")
            participant.pay_perc = submitted.get(f"pay_perc_{participant.id}", "")
            continue

        total = expense.amount if expense is not None else None
        owed = saved_splits.get(participant.id)
        participant.split_value = _money_input(owed)
        participant.split_perc = _perc_input(owed, total)
        paid = saved_payers.get(participant.id)
        participant.pay_value = _money_input(paid)
        participant.pay_perc = _perc_input(paid, total)

    return {
        "group": group,
        "participants": participants,
        "included_count": len(included),
        "paying_count": len(paying),
        "expense": expense,
        "initial": initial,
    }


@login_required
def add_expense(request, group_id):
    group = get_object_or_404(Group, pk=group_id)

    if not _can_manage(group, request.user) and not _my_participant(group, request.user):
        messages.error(request, "Você não participa deste grupo.")
        return redirect("rachais:group_detail", group_id=group.id)

    participants = list(
        group.participants.filter(left_at__isnull=True).select_related("user").order_by("id")
    )

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

    # Quem já saiu do grupo continua aparecendo nas despesas em que estava,
    # para a edição não sumir com a parte dela.
    involved = (
        set(expense.splits.values_list("participant_id", flat=True))
        | set(expense.payers.values_list("participant_id", flat=True))
        | {expense.paid_by_id}
    )
    participants = list(
        group.participants.filter(Q(left_at__isnull=True) | Q(pk__in=involved))
        .select_related("user").order_by("id")
    )

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
