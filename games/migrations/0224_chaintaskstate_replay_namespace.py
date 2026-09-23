from django.db import migrations, models


def backfill_namespace_and_merge_duplicates(apps, schema_editor):
    ChainTaskState = apps.get_model('games', 'ChainTaskState')

    # The previous unique constraints included nullable replay_slot.  On
    # MySQL that allowed several official rows (replay_slot IS NULL) for the
    # same actor/task/mode.  Preserve the row with the latest committed
    # attempt; an empty duplicate is the usual result of the race.
    groups = {}
    for row in ChainTaskState.objects.all().iterator():
        row.replay_slot_key = row.replay_slot_id or 0
        row.save(update_fields=['replay_slot_key'])
        actor = ('team', row.team_id) if row.team_id is not None else (
            'user', row.user_id) if row.user_id is not None else ('anon', row.anon_key)
        key = (actor, row.task_id, row.game_id, row.game_mode, row.replay_slot_key)
        groups.setdefault(key, []).append(row)

    for rows in groups.values():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda row: (
            row.last_attempt_id is not None,
            row.last_attempt_id or 0,
            row.updated_at,
            row.pk,
        ), reverse=True)
        for duplicate in rows[1:]:
            duplicate.delete()


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0223_anonymous_merge_queue'),
    ]

    operations = [
        migrations.AddField(
            model_name='chaintaskstate',
            name='replay_slot_key',
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
        migrations.RunPython(
            backfill_namespace_and_merge_duplicates,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name='chaintaskstate',
            name='unique_chain_state_team_game',
        ),
        migrations.RemoveConstraint(
            model_name='chaintaskstate',
            name='unique_chain_state_user_game',
        ),
        migrations.RemoveConstraint(
            model_name='chaintaskstate',
            name='unique_chain_state_anon_key_game',
        ),
        migrations.AddConstraint(
            model_name='chaintaskstate',
            constraint=models.UniqueConstraint(
                condition=models.Q(team__isnull=False),
                fields=('team', 'task', 'game', 'game_mode', 'replay_slot_key'),
                name='unique_chain_state_team_game',
            ),
        ),
        migrations.AddConstraint(
            model_name='chaintaskstate',
            constraint=models.UniqueConstraint(
                condition=models.Q(user__isnull=False),
                fields=('user', 'task', 'game', 'game_mode', 'replay_slot_key'),
                name='unique_chain_state_user_game',
            ),
        ),
        migrations.AddConstraint(
            model_name='chaintaskstate',
            constraint=models.UniqueConstraint(
                condition=models.Q(anon_key__isnull=False),
                fields=('anon_key', 'task', 'game', 'game_mode', 'replay_slot_key'),
                name='unique_chain_state_anon_key_game',
            ),
        ),
    ]
