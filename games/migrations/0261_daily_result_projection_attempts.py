from django.db import migrations, models


def invalidate_existing_projections(apps, schema_editor):
    State = apps.get_model('games', 'DailyResultProjectionState')
    State.objects.update(
        is_valid=False,
        full_refresh_required=True,
        pending_actor_refreshes=0,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0260_daily_result_projection_dirty_actor'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyresultprojection',
            name='attempts_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(invalidate_existing_projections, migrations.RunPython.noop),
    ]
