from django.db import migrations
from django.db.models import Count
import json


PROGRESS_LIST_KEYS = (
    'solved_indices', 'found_rare', 'found_rare_words', 'found_extra',
    'used_hints', 'hints',
)


def _merged_state(rows):
    """Keep the newest state while retaining monotonic progress fields."""
    newest = max(rows, key=lambda row: (row.updated_at, row.pk))
    try:
        merged = json.loads(newest.state or '{}')
    except (TypeError, ValueError):
        merged = {}
    if not isinstance(merged, dict):
        merged = {}

    for key in PROGRESS_LIST_KEYS:
        values = []
        for row in rows:
            try:
                state = json.loads(row.state or '{}')
            except (TypeError, ValueError):
                state = {}
            for value in state.get(key) or []:
                if value not in values:
                    values.append(value)
        if values:
            merged[key] = values
    return json.dumps(merged, separators=(',', ':'))


def merge_duplicate_chain_states(apps, schema_editor):
    ChainTaskState = apps.get_model('games', 'ChainTaskState')
    duplicate_groups = ChainTaskState.objects.values(
        'team_id', 'user_id', 'anon_key', 'task_id', 'game_id',
        'game_mode', 'replay_slot_key',
    ).annotate(n=Count('pk')).filter(n__gt=1)

    for key in duplicate_groups.iterator():
        rows = list(ChainTaskState.objects.filter(
            team_id=key['team_id'], user_id=key['user_id'],
            anon_key=key['anon_key'], task_id=key['task_id'],
            game_id=key['game_id'], game_mode=key['game_mode'],
            replay_slot_key=key['replay_slot_key'],
        ).order_by('updated_at', 'pk'))
        if len(rows) < 2:
            continue
        keeper = max(rows, key=lambda row: (row.updated_at, row.pk))
        keeper.state = _merged_state(rows)
        keeper.actor_key = (
            'team:{}'.format(keeper.team_id)
            if keeper.team_id is not None else
            'user:{}'.format(keeper.user_id)
            if keeper.user_id is not None else
            'anon:{}'.format(keeper.anon_key)
        )
        keeper.save(update_fields=['state', 'actor_key', 'updated_at'])
        ChainTaskState.objects.filter(
            pk__in=[row.pk for row in rows if row.pk != keeper.pk],
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0235_attempt_games_attempt_status_idx'),
    ]

    operations = [
        migrations.RunPython(merge_duplicate_chain_states, migrations.RunPython.noop),
    ]
