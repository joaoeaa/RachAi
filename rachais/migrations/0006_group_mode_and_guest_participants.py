from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Etapa 1/3 da introdução de participantes avulsos (sem conta).

    Aqui apenas ADICIONAMOS: o modo do grupo, o nome do participante avulso e as
    novas chaves apontando para Participant. Nada é removido ainda, para que a
    migração de dados (0007) possa ler os valores antigos.
    """

    dependencies = [
        ('rachais', '0005_payment'),
    ]

    operations = [
        migrations.AddField(
            model_name='group',
            name='mode',
            field=models.CharField(
                choices=[
                    ('SHARED', 'Colaborativo — cada participante entra com sua conta'),
                    ('MANAGED', 'Gerenciado por mim — eu cadastro os nomes e os gastos'),
                ],
                default='SHARED',
                max_length=10,
                verbose_name='Tipo do grupo',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='participant',
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name='participant',
            name='user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='group_participations',
                to='auth.user',
            ),
        ),
        migrations.AddField(
            model_name='participant',
            name='guest_name',
            field=models.CharField(
                blank=True,
                default='',
                max_length=100,
                verbose_name='Nome do participante avulso',
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='expense',
            name='paid_by_tmp',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='+',
                to='rachais.participant',
            ),
        ),
        migrations.AddField(
            model_name='expensesplit',
            name='participant',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='splits',
                to='rachais.participant',
            ),
        ),
        migrations.AddField(
            model_name='payment',
            name='payer_tmp',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='+',
                to='rachais.participant',
            ),
        ),
        migrations.AddField(
            model_name='payment',
            name='receiver_tmp',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='+',
                to='rachais.participant',
            ),
        ),
    ]
