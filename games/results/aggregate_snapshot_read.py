"""Bounded SQL paging over one immutable monthly table publication."""

import hashlib
import json
from django.db import connection

from games.models import DailyAggregateSnapshotRow


def read_snapshot_page(version, page_number, actor_types, aggregate_sort, *, page_size=50):
    if not actor_types:
        return [], 0, 1, {}
    q = connection.ops.quote_name
    table = q(DailyAggregateSnapshotRow._meta.db_table)
    team, profile, hidden = q('games_team'), q('games_profile'), q('games_hiddenanonkey')
    marks = ', '.join(['%s'] * len(actor_types))
    eligible = f'''s.version_id = %s AND s.actor_type IN ({marks})
        AND (s.team_id IS NULL OR EXISTS (
            SELECT 1 FROM {team} t WHERE t.name = s.team_id AND t.is_hidden = %s))
        AND (s.user_id IS NULL OR EXISTS (
            SELECT 1 FROM {profile} p WHERE p.user_id = s.user_id AND p.is_hidden = %s))
        AND (s.anon_key IS NULL OR NOT EXISTS (
            SELECT 1 FROM {hidden} h WHERE h.anon_key = s.anon_key))'''
    # UUIDField is stored without hyphens by MySQL and SQLite. Let the field
    # adapt it for the backend instead of depending on its physical type.
    field = DailyAggregateSnapshotRow._meta.get_field('version').target_field
    version_id = field.get_db_prep_value(version.pk, connection)
    params = [version_id, *sorted(actor_types), False, False]
    order = ('attempts_count, total_time_ms IS NULL, total_time_ms'
             if aggregate_sort == 'attempts'
             else 'total_time_ms IS NULL, total_time_ms, attempts_count')
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT COUNT(*) FROM {table} s WHERE {eligible}', params)
        total = cursor.fetchone()[0]
        # Visibility changes immediately, so a page fetched later must not be
        # appended if actors disappeared and shifted the offsets. Hash only
        # identities excluded by current visibility rules, not the full table.
        hidden_predicate = f'''s.version_id = %s AND s.actor_type IN ({marks}) AND (
            (s.team_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM {team} t WHERE t.name = s.team_id AND t.is_hidden = %s)) OR
            (s.user_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM {profile} p WHERE p.user_id = s.user_id AND p.is_hidden = %s)) OR
            (s.anon_key IS NOT NULL AND EXISTS (
                SELECT 1 FROM {hidden} h WHERE h.anon_key = s.anon_key)))'''
        cursor.execute(
            f'SELECT s.actor_type, s.actor_key FROM {table} s WHERE {hidden_predicate} ORDER BY s.actor_type, s.actor_key',
            [version_id, *sorted(actor_types), False, False],
        )
        hidden_identities = [f'{kind}:{key}' for kind, key in cursor.fetchall()]
        page = min(max(1, page_number), max(1, (total + page_size - 1) // page_size))
        cursor.execute(f'''WITH ranked AS (
            SELECT s.actor_type, s.actor_key, s.team_id, s.user_id, s.anon_key,
                   s.score AS window_score, s.attempts_count, s.played_count,
                   s.total_time_ms, s.used_fallback_time, s.cells,
                   RANK() OVER (ORDER BY s.score DESC) AS place
              FROM {table} s WHERE {eligible}
        ) SELECT * FROM ranked
          ORDER BY window_score DESC, {order}, actor_type, actor_key LIMIT %s OFFSET %s''',
                       [*params, page_size, (page - 1) * page_size])
        names = [col[0] for col in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    cells = {}
    for row in rows:
        data = row.pop('cells') or {}
        if isinstance(data, str):
            data = json.loads(data)
        cells[(row['actor_type'], row['actor_key'])] = {int(key): value for key, value in data.items()}
    view = json.dumps(
        [str(version.pk), sorted(actor_types), total, hidden_identities],
        ensure_ascii=False, separators=(',', ':'),
    )
    view_token = hashlib.sha256(view.encode('utf-8')).hexdigest()
    return rows, total, page, cells, view_token
