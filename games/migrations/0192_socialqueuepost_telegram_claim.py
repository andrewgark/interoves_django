from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0191_daily_difficulty_scheduler'),
    ]

    operations = [
        migrations.AddField(
            model_name='socialqueuepost',
            name='telegram_claim_token',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='socialqueuepost',
            name='telegram_claimed_at',
            field=models.DateTimeField(
                blank=True,
                help_text='Lease timestamp for an in-progress Telegram delivery',
                null=True,
            ),
        ),
    ]
