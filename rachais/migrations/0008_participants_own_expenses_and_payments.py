from django.db import migrations, models
import django.db.models.deletion
import django.db.models.functions.text


class Migration(migrations.Migration):
    """
    Etapa 3/3: as despesas, divisões e pagamentos passam a apontar para Participant
    em vez de User, e as antigas chaves para User são removidas.

    Atenção: esta migração é somente de ida. Participantes avulsos não têm
    equivalente em User, então as colunas antigas não podem ser recriadas com dados.
    Reverter (`migrate rachais 0007`) falha de propósito, dentro de uma transação,
    sem deixar o banco em estado inconsistente.
    """

    dependencies = [
        ('rachais', '0007_link_records_to_participants'),
    ]

    operations = [
        # --- Expense.participants (M2M) passa a atravessar Participant ---
        migrations.AlterField(
            model_name='expense',
            name='participants',
            field=models.ManyToManyField(
                related_name='expenses_participated',
                through='rachais.ExpenseSplit',
                to='rachais.participant',
            ),
        ),

        # --- ExpenseSplit: user -> participant ---
        migrations.AlterUniqueTogether(
            name='expensesplit',
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name='expensesplit',
            name='user',
        ),
        migrations.AlterField(
            model_name='expensesplit',
            name='participant',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='splits',
                to='rachais.participant',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='expensesplit',
            unique_together={('expense', 'participant')},
        ),

        # --- Expense.paid_by -> Participant ---
        migrations.RemoveField(
            model_name='expense',
            name='paid_by',
        ),
        migrations.RenameField(
            model_name='expense',
            old_name='paid_by_tmp',
            new_name='paid_by',
        ),
        migrations.AlterField(
            model_name='expense',
            name='paid_by',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='paid_expenses',
                to='rachais.participant',
                verbose_name='Quem pagou',
            ),
        ),

        # --- Payment.payer / Payment.receiver -> Participant ---
        migrations.RemoveField(
            model_name='payment',
            name='payer',
        ),
        migrations.RemoveField(
            model_name='payment',
            name='receiver',
        ),
        migrations.RenameField(
            model_name='payment',
            old_name='payer_tmp',
            new_name='payer',
        ),
        migrations.RenameField(
            model_name='payment',
            old_name='receiver_tmp',
            new_name='receiver',
        ),
        migrations.AlterField(
            model_name='payment',
            name='payer',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='payments_made',
                to='rachais.participant',
            ),
        ),
        migrations.AlterField(
            model_name='payment',
            name='receiver',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='payments_received',
                to='rachais.participant',
            ),
        ),

        # --- Regras de integridade do Participant ---
        migrations.AddConstraint(
            model_name='participant',
            constraint=models.UniqueConstraint(
                condition=models.Q(('user__isnull', False)),
                fields=('group', 'user'),
                name='uniq_participant_user_per_group',
            ),
        ),
        migrations.AddConstraint(
            model_name='participant',
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower('guest_name'),
                models.F('group'),
                condition=models.Q(('guest_name', ''), _negated=True),
                name='uniq_participant_guest_name_per_group_ci',
            ),
        ),
        migrations.AddConstraint(
            model_name='participant',
            constraint=models.CheckConstraint(
                condition=models.Q(('user__isnull', False), models.Q(('guest_name', ''), _negated=True), _connector='OR'),
                name='participant_has_user_or_guest_name',
            ),
        ),
    ]
