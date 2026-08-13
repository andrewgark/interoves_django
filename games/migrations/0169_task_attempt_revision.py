import uuid

from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def copy_current_task_revision_to_attempts(apps, schema_editor):
    """Keep duplicate behaviour unchanged until a task is saved again."""
    Attempt = apps.get_model('games', 'Attempt')
    Task = apps.get_model('games', 'Task')

    task_revision = Task.objects.filter(pk=OuterRef('task_id')).values('attempt_revision')[:1]
    Attempt._base_manager.filter(task_id__isnull=False).update(
        task_revision=Subquery(task_revision),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0168_product_analytics_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='attempt_revision',
            field=models.UUIDField(default=uuid.uuid4, editable=False),
        ),
        migrations.AddField(
            model_name='attempt',
            name='task_revision',
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(
            copy_current_task_revision_to_attempts,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
