from django.db import migrations, models


def forwards_contact_fields(apps, schema_editor):
    CorporateGameOrder = apps.get_model('games', 'CorporateGameOrder')
    for order in CorporateGameOrder.objects.all():
        preferred = getattr(order, 'preferred_contact', '') or 'email'
        email = getattr(order, 'email', '') or ''
        telegram = getattr(order, 'telegram', '') or ''

        if preferred == 'telegram' and telegram:
            order.contact_method = 'telegram'
            order.contact_value = telegram
        elif preferred == 'email' and email:
            order.contact_method = 'email'
            order.contact_value = email
        elif preferred == 'other':
            order.contact_method = 'other'
            order.contact_value = telegram or email or '—'
        elif telegram:
            order.contact_method = 'telegram'
            order.contact_value = telegram
        else:
            order.contact_method = 'email'
            order.contact_value = email
        order.save(update_fields=['contact_method', 'contact_value', 'contact_other_label'])


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0127_corporategameorder_telegram_contact'),
    ]

    operations = [
        migrations.AddField(
            model_name='corporategameorder',
            name='contact_method',
            field=models.CharField(
                choices=[('telegram', 'Telegram'), ('email', 'Email'), ('other', 'Другое')],
                default='telegram',
                max_length=20,
                verbose_name='способ связи',
            ),
        ),
        migrations.AddField(
            model_name='corporategameorder',
            name='contact_value',
            field=models.CharField(default='', max_length=254, verbose_name='контакт'),
        ),
        migrations.AddField(
            model_name='corporategameorder',
            name='contact_other_label',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Для «Другое»: WhatsApp, VK и т.п.',
                max_length=100,
                verbose_name='тип контакта',
            ),
        ),
        migrations.RunPython(forwards_contact_fields, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='corporategameorder',
            name='email',
        ),
        migrations.RemoveField(
            model_name='corporategameorder',
            name='preferred_contact',
        ),
        migrations.RemoveField(
            model_name='corporategameorder',
            name='telegram',
        ),
    ]
