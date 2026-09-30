from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('games', '0248_subscriptiongift_optional_telegram_recipient')]

    operations = [
        migrations.AddField(
            model_name='gametaskgroup',
            name='is_deferred',
            field=models.BooleanField(default=False, help_text='Временно убрано из расписания support.'),
        ),
    ]
