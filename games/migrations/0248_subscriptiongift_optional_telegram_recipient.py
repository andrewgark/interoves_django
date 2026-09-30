from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0247_random_alphabetty_game'),
    ]

    operations = [
        migrations.AlterField(
            model_name='subscriptiongift',
            name='recipient_telegram_user_id',
            field=models.BigIntegerField(blank=True, null=True, db_index=True),
        ),
    ]
