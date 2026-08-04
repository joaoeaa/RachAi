from django.db import migrations


def _participant_for(Participant, cache, group_id, user_id):
    """Participant do usuário naquele grupo, criando-o se por algum motivo faltar."""
    key = (group_id, user_id)
    if key not in cache:
        participant, _ = Participant.objects.get_or_create(
            group_id=group_id,
            user_id=user_id,
            defaults={'guest_name': ''},
        )
        cache[key] = participant.pk
    return cache[key]


def forwards(apps, schema_editor):
    Participant = apps.get_model('rachais', 'Participant')
    Expense = apps.get_model('rachais', 'Expense')
    ExpenseSplit = apps.get_model('rachais', 'ExpenseSplit')
    Payment = apps.get_model('rachais', 'Payment')

    cache = {
        (p.group_id, p.user_id): p.pk
        for p in Participant.objects.filter(user_id__isnull=False)
    }

    for expense in Expense.objects.all().iterator():
        expense.paid_by_tmp_id = _participant_for(
            Participant, cache, expense.group_id, expense.paid_by_id
        )
        expense.save(update_fields=['paid_by_tmp'])

    for split in ExpenseSplit.objects.select_related('expense').iterator():
        split.participant_id = _participant_for(
            Participant, cache, split.expense.group_id, split.user_id
        )
        split.save(update_fields=['participant'])

    for payment in Payment.objects.all().iterator():
        payment.payer_tmp_id = _participant_for(
            Participant, cache, payment.group_id, payment.payer_id
        )
        payment.receiver_tmp_id = _participant_for(
            Participant, cache, payment.group_id, payment.receiver_id
        )
        payment.save(update_fields=['payer_tmp', 'receiver_tmp'])


def backwards(apps, schema_editor):
    """
    Volta as chaves para User. Participantes avulsos não têm equivalente em User,
    então registros que dependam deles não podem ser revertidos.
    """
    Expense = apps.get_model('rachais', 'Expense')
    ExpenseSplit = apps.get_model('rachais', 'ExpenseSplit')
    Payment = apps.get_model('rachais', 'Payment')

    for expense in Expense.objects.select_related('paid_by_tmp').iterator():
        if expense.paid_by_tmp and expense.paid_by_tmp.user_id:
            expense.paid_by_id = expense.paid_by_tmp.user_id
            expense.save(update_fields=['paid_by'])

    for split in ExpenseSplit.objects.select_related('participant').iterator():
        if split.participant and split.participant.user_id:
            split.user_id = split.participant.user_id
            split.save(update_fields=['user'])

    for payment in Payment.objects.select_related('payer_tmp', 'receiver_tmp').iterator():
        if (
            payment.payer_tmp and payment.payer_tmp.user_id
            and payment.receiver_tmp and payment.receiver_tmp.user_id
        ):
            payment.payer_id = payment.payer_tmp.user_id
            payment.receiver_id = payment.receiver_tmp.user_id
            payment.save(update_fields=['payer', 'receiver'])


class Migration(migrations.Migration):
    """Etapa 2/3: preenche as novas chaves a partir das antigas."""

    dependencies = [
        ('rachais', '0006_group_mode_and_guest_participants'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
