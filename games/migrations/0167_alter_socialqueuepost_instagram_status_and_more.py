from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0166_ticket_payment_routes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='socialqueuepost',
            name='instagram_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('queued', 'Queued (internal schedule)'),
                    ('publishing', 'Publishing'),
                    ('sent', 'Sent'),
                    ('failed', 'Failed'),
                    ('skipped', 'Skipped'),
                ],
                default='pending',
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name='socialqueuepost',
            name='telegram_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('queued', 'Queued (internal schedule)'),
                    ('publishing', 'Publishing'),
                    ('scheduled', 'Scheduled in Telegram'),
                    ('sent', 'Sent'),
                    ('failed', 'Failed'),
                    ('skipped', 'Skipped'),
                ],
                default='pending',
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name='socialqueuepost',
            name='twitter_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('queued', 'Queued (internal schedule)'),
                    ('publishing', 'Publishing'),
                    ('sent', 'Sent'),
                    ('failed', 'Failed'),
                    ('skipped', 'Skipped'),
                ],
                default='pending',
                max_length=16,
            ),
        ),
    ]
