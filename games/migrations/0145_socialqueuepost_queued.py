from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0144_socialqueuepost'),
    ]

    operations = [
        migrations.AddField(
            model_name='socialqueuepost',
            name='instagram_queued_for',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='socialqueuepost',
            name='telegram_queued_for',
            field=models.DateTimeField(
                blank=True,
                help_text='Internal schedule: minute cron publishes to the channel at this time',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='socialqueuepost',
            name='twitter_queued_for',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='socialqueuepost',
            name='instagram_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('queued', 'Queued (internal schedule)'),
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
            name='telegram_scheduled_for',
            field=models.DateTimeField(
                blank=True,
                help_text='When the post sits in Telegram native deferred messages',
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='socialqueuepost',
            name='telegram_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('queued', 'Queued (internal schedule)'),
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
                    ('sent', 'Sent'),
                    ('failed', 'Failed'),
                    ('skipped', 'Skipped'),
                ],
                default='pending',
                max_length=16,
            ),
        ),
    ]
