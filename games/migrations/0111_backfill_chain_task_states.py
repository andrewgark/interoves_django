from django.db import migrations
from django.core.management import call_command


def backfill_chain_states(apps, schema_editor):
    call_command('backfill_chain_task_states')


def reverse_backfill(apps, schema_editor):
    # Restore the pre-backfill state: empty ChainTaskState table.
    ChainTaskState = apps.get_model('games', 'ChainTaskState')
    ChainTaskState.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0110_chaintaskstate'),
    ]

    operations = [
        migrations.RunPython(backfill_chain_states, reverse_code=reverse_backfill),
    ]
