from django.db import migrations, models


def backfill_actor_keys_and_reject_duplicates(apps, schema_editor):
    ChainTaskState = apps.get_model('games', 'ChainTaskState')
    seen = set()
    rows = ChainTaskState.objects.all().order_by('pk').iterator()
    for row in rows:
        if row.team_id is not None:
            actor_key = 'team:{}'.format(row.team_id)
        elif row.user_id is not None:
            actor_key = 'user:{}'.format(row.user_id)
        elif row.anon_key is not None:
            actor_key = 'anon:{}'.format(row.anon_key)
        else:
            raise RuntimeError(
                'Cannot backfill ChainTaskState.actor_key for row {}'.format(row.pk)
            )
        logical_key = (
            actor_key, row.task_id, row.game_id, row.game_mode, row.replay_slot_key,
        )
        if logical_key in seen:
            raise RuntimeError(
                'Duplicate ChainTaskState logical key before unique constraint: {}'.format(
                    logical_key,
                )
            )
        seen.add(logical_key)
        ChainTaskState.objects.filter(pk=row.pk).update(actor_key=actor_key)


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0233_wordsaladrecheckjob_pending_resolution'),
    ]

    operations = [
        migrations.AddField(
            model_name='chaintaskstate',
            name='actor_key',
            field=models.CharField(editable=False, max_length=128, null=True),
        ),
        migrations.RunPython(
            backfill_actor_keys_and_reject_duplicates,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='chaintaskstate',
            name='actor_key',
            field=models.CharField(editable=False, max_length=128),
        ),
        migrations.AddConstraint(
            model_name='chaintaskstate',
            constraint=models.UniqueConstraint(
                fields=('actor_key', 'task', 'game', 'game_mode', 'replay_slot_key'),
                name='unique_chain_state_actor_context',
            ),
        ),
    ]
