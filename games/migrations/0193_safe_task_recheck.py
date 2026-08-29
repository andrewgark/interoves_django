from django.db import migrations, models


def copy_current_attempt_verdicts(apps, schema_editor):
    Attempt = apps.get_model('games', 'Attempt')
    Attempt._base_manager.update(
        current_status=models.F('status'),
        current_points=models.F('points'),
        checked_revision=models.F('task_revision'),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0192_socialqueuepost_telegram_claim'),
    ]

    operations = [
        migrations.AddField(
            model_name='attempt',
            name='checked_revision',
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='attempt',
            name='current_points',
            field=models.DecimalField(blank=True, decimal_places=3, default=0, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='attempt',
            name='current_status',
            field=models.CharField(blank=True, choices=[('Ok', 'Ok'), ('Pending', 'Pending'), ('Partial', 'Partial'), ('Wrong', 'Wrong')], max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='attempt',
            name='recheck_points_floor',
            field=models.DecimalField(blank=True, decimal_places=3, default=0, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='chaintaskstate',
            name='completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='chaintaskstate',
            name='completed_revision',
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='chaintaskstate',
            name='validated_revision',
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(
            copy_current_attempt_verdicts,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
