"""Bounded SQL paging over one immutable monthly table publication."""

import hashlib
import json

from django.db import connection

from games.models import DailyAggregateSnapshotRow


def _snapshot_view_token(version, actor_types, total, hidden_identities, scope_key=''):
    view = json.dumps(
        [str(version.pk), sorted(actor_types), total, hidden_identities, scope_key],
        ensure_ascii=False, separators=(',', ':'),
    )
    return hashlib.sha256(view.encode('utf-8')).hexdigest()


# Monthly tables rank by averages over the releases an actor could play
# (every release of the month except the ones they wrote; a skipped release
# counts as zero), so writing a release no longer costs its author places.
# Rows from versions built before averages have eligible_count = 0 and keep
# their sums until the month republishes. ``* 1.0`` avoids SQLite integer
# division on whole-number scores.
AVG_SCORE_SQL = '(CASE WHEN s.eligible_count > 0 THEN s.score * 1.0 / s.eligible_count ELSE s.score END)'
AVG_ATTEMPTS_SQL = ('(CASE WHEN s.eligible_count > 0 AND s.played_count > 0 '
                    'THEN s.attempts_count * 1.0 / s.played_count ELSE s.attempts_count END)')
AVG_TIME_SQL = ('(CASE WHEN s.eligible_count > 0 AND s.timed_count > 0 '
                'THEN s.total_time_ms * 1.0 / s.timed_count ELSE s.total_time_ms END)')


def tie_order_sql(aggregate_sort):
    if aggregate_sort == 'attempts':
        return 'avg_attempts, avg_time IS NULL, avg_time'
    return 'avg_time IS NULL, avg_time, avg_attempts'


def read_snapshot_page(version, page_number, actor_types, aggregate_sort, *, page_size=50, scope=None):
    """One page of the published table; ``scope`` filters rows after ranking.

    Places are always ranked over every eligible row, so a friends-only view
    shows each friend's global place.
    """
    scope_key = scope.key if scope is not None else ''
    if not actor_types:
        return [], 0, 1, {}, _snapshot_view_token(version, actor_types, 0, [], scope_key)
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
    order = tie_order_sql(aggregate_sort)
    scope_sql, scope_params, scope_row_sql = '1 = 1', [], '1 = 1'
    if scope is not None:
        from games.community.results_scope import sql_filter

        scope_sql, scope_params = sql_filter(scope)
        scope_row_sql, _ = sql_filter(scope, 's.actor_type', 's.actor_key')
    with connection.cursor() as cursor:
        cursor.execute(
            f'SELECT COUNT(*) FROM {table} s WHERE {eligible} AND {scope_row_sql}',
            [*params, *scope_params],
        )
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
                   {AVG_SCORE_SQL} AS window_score, s.score AS total_score,
                   {AVG_ATTEMPTS_SQL} AS avg_attempts, {AVG_TIME_SQL} AS avg_time,
                   s.attempts_count, s.played_count, s.eligible_count, s.timed_count,
                   s.total_time_ms, s.used_fallback_time, s.cells,
                   RANK() OVER (ORDER BY {AVG_SCORE_SQL} DESC) AS place
              FROM {table} s WHERE {eligible}
        ) SELECT * FROM ranked WHERE {scope_sql}
          ORDER BY window_score DESC, {order}, actor_type, actor_key LIMIT %s OFFSET %s''',
                       [*params, *scope_params, page_size, (page - 1) * page_size])
        names = [col[0] for col in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    cells = {}
    for row in rows:
        data = row.pop('cells') or {}
        if isinstance(data, str):
            data = json.loads(data)
        cells[(row['actor_type'], row['actor_key'])] = {int(key): value for key, value in data.items()}
    view_token = _snapshot_view_token(version, actor_types, total, hidden_identities, scope_key)
    return rows, total, page, cells, view_token


ALL_ACTOR_TYPES = ('anon', 'team', 'user')


def snapshot_actor_place(version_id, actor_type, actor_key, aggregate_sort):
    """``{'place', 'place_tie', 'total'}`` of one actor in the full table.

    ``place`` is the table's place (by average score); ``place_tie`` also
    orders equal averages by the table's tiebreak. ``None`` when the actor
    is not in the public table (no row, or hidden).
    """
    q = connection.ops.quote_name
    table = q(DailyAggregateSnapshotRow._meta.db_table)
    team, profile, hidden = q('games_team'), q('games_profile'), q('games_hiddenanonkey')
    marks = ', '.join(['%s'] * len(ALL_ACTOR_TYPES))
    field = DailyAggregateSnapshotRow._meta.get_field('version').target_field
    db_version = field.get_db_prep_value(version_id, connection)
    with connection.cursor() as cursor:
        cursor.execute(f'''WITH ranked AS (
            SELECT s.actor_type, s.actor_key,
                   {AVG_SCORE_SQL} AS avg_score,
                   {AVG_ATTEMPTS_SQL} AS avg_attempts, {AVG_TIME_SQL} AS avg_time
              FROM {table} s
             WHERE s.version_id = %s AND s.actor_type IN ({marks})
               AND (s.team_id IS NULL OR EXISTS (
                   SELECT 1 FROM {team} t WHERE t.name = s.team_id AND t.is_hidden = %s))
               AND (s.user_id IS NULL OR EXISTS (
                   SELECT 1 FROM {profile} p WHERE p.user_id = s.user_id AND p.is_hidden = %s))
               AND (s.anon_key IS NULL OR NOT EXISTS (
                   SELECT 1 FROM {hidden} h WHERE h.anon_key = s.anon_key))
        ), placed AS (
            SELECT actor_type, actor_key,
                   RANK() OVER (ORDER BY avg_score DESC) AS place,
                   RANK() OVER (ORDER BY avg_score DESC, {tie_order_sql(aggregate_sort)}) AS place_tie,
                   COUNT(*) OVER () AS total
              FROM ranked
        ) SELECT place, place_tie, total FROM placed WHERE actor_type = %s AND actor_key = %s''',
            [db_version, *ALL_ACTOR_TYPES, False, False, actor_type, str(actor_key)])
        row = cursor.fetchone()
    if row is None:
        return None
    return {'place': int(row[0]), 'place_tie': int(row[1]), 'total': int(row[2])}
