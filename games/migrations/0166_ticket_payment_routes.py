from django.db import migrations, models


def backfill_payment_routes(apps, schema_editor):
    Team = apps.get_model('games', 'Team')
    TicketRequest = apps.get_model('games', 'TicketRequest')

    Team.objects.filter(ticket_price=500).update(ticket_price_amd=2500)
    Team.objects.exclude(ticket_price=500).update(ticket_price_amd=10000)

    TicketRequest.objects.update(
        currency='RUB',
        payment_provider='manual',
        merchant='ru_self_employed',
    )
    TicketRequest.objects.exclude(yookassa_id__isnull=True).exclude(yookassa_id='').update(
        payment_provider='yookassa',
        merchant='ru_self_employed',
    )
    TicketRequest.objects.exclude(nowpayments_id__isnull=True).exclude(nowpayments_id='').update(
        payment_provider='nowpayments',
        merchant='ru_self_employed',
    )
    TicketRequest.objects.exclude(tribute_id__isnull=True).exclude(tribute_id='').update(
        payment_provider='tribute',
        merchant='legacy_unspecified',
    )


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0165_alphabetty_offer'),
    ]

    operations = [
        migrations.AddField(
            model_name='team',
            name='ticket_price_amd',
            field=models.IntegerField(default=10000),
        ),
        migrations.AddField(
            model_name='ticketrequest',
            name='currency',
            field=models.CharField(
                choices=[('RUB', 'RUB'), ('AMD', 'AMD')],
                default='RUB',
                max_length=3,
            ),
        ),
        migrations.AddField(
            model_name='ticketrequest',
            name='merchant',
            field=models.CharField(
                choices=[
                    ('ru_self_employed', 'Андрей Гаркавый, плательщик НПД, РФ'),
                    ('am_ie', 'Andrei Garkavyi IE, Republic of Armenia'),
                    ('legacy_unspecified', 'Legacy / unspecified'),
                ],
                default='ru_self_employed',
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='ticketrequest',
            name='payment_provider',
            field=models.CharField(
                choices=[
                    ('manual', 'Manual / legacy'),
                    ('yookassa', 'YooKassa'),
                    ('nowpayments', 'NOWPayments'),
                    ('tribute', 'Tribute (legacy)'),
                    ('vpos', 'Armenian acquiring / VPOS'),
                ],
                default='manual',
                max_length=32,
            ),
        ),
        migrations.RunPython(backfill_payment_routes, migrations.RunPython.noop),
    ]
