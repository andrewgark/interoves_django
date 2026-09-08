from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('games', '0202_telegramadminreport')]

    operations = [
        migrations.AddField(
            model_name='socialqueuepost', name='threads_status',
            field=models.CharField(max_length=16, choices=[
                ('pending', 'Pending'), ('queued', 'Queued (internal schedule)'),
                ('publishing', 'Publishing'), ('sent', 'Sent'), ('failed', 'Failed'),
                ('skipped', 'Skipped'),
            ], default='pending'),
        ),
        migrations.AddField(
            model_name='socialqueuepost', name='threads_external_id',
            field=models.CharField(max_length=64, blank=True, default=''),
        ),
        migrations.AddField(
            model_name='socialqueuepost', name='threads_error',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='socialqueuepost', name='threads_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='socialqueuepost', name='threads_queued_for',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='socialqueuepost', name='threads_attempts',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
