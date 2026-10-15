"""Read-only key-coverage audit for the historical chain-state backfill.

The default mode prints an optimizer plan only. ``--execute`` runs a full
aggregate comparison over source attempts and state rows; a statement timeout
does not bound intermediate memory or total rows examined. It never repairs
rows or calls recheck code.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection


AUDIT_SQL = """
WITH group_game_map AS (
    SELECT task_group_id, MIN(game_id) AS game_id
    FROM games_gametaskgroup
    GROUP BY task_group_id
    HAVING COUNT(*) = 1
), expected_contexts AS (
    SELECT DISTINCT
        a.team_id, a.user_id, a.anon_key, a.task_id,
        COALESCE(a.game_id, gm.game_id) AS game_id,
        a.replay_slot_id
    FROM games_attempt a
    JOIN games_task t ON t.id = a.task_id
    LEFT JOIN group_game_map gm ON gm.task_group_id = t.task_group_id
    WHERE t.task_type IN ('wall', 'replacements_lines')
      AND (a.game_id IS NOT NULL OR gm.game_id IS NOT NULL)
), expected_rows AS (
    SELECT e.*, modes.game_mode
    FROM expected_contexts e
    CROSS JOIN (
        SELECT 'general' AS game_mode
        UNION ALL SELECT 'tournament'
    ) modes
), unresolved AS (
    SELECT COUNT(*) AS n
    FROM games_attempt a
    JOIN games_task t ON t.id = a.task_id
    LEFT JOIN group_game_map gm ON gm.task_group_id = t.task_group_id
    WHERE t.task_type IN ('wall', 'replacements_lines')
      AND a.game_id IS NULL AND gm.game_id IS NULL
), invalid_actors AS (
    SELECT COUNT(*) AS n
    FROM expected_contexts e
    WHERE (e.team_id IS NOT NULL) + (e.user_id IS NOT NULL) +
          (e.anon_key IS NOT NULL AND e.anon_key <> '') <> 1
), missing AS (
    SELECT COUNT(*) AS n
    FROM expected_rows e
    LEFT JOIN games_chaintaskstate s
      ON s.team_id <=> e.team_id
     AND s.user_id <=> e.user_id
     AND s.anon_key <=> e.anon_key
     AND s.task_id = e.task_id
     AND s.game_id = e.game_id
     AND s.replay_slot_key = COALESCE(e.replay_slot_id, 0)
     AND s.game_mode = e.game_mode
    WHERE s.id IS NULL
), extras AS (
    SELECT COUNT(*) AS n
    FROM games_chaintaskstate s
    JOIN games_task t ON t.id = s.task_id
    LEFT JOIN expected_contexts e
      ON e.team_id <=> s.team_id
     AND e.user_id <=> s.user_id
     AND e.anon_key <=> s.anon_key
     AND e.task_id = s.task_id
     AND e.game_id = s.game_id
     AND COALESCE(e.replay_slot_id, 0) = s.replay_slot_key
    WHERE t.task_type IN ('wall', 'replacements_lines')
      AND e.task_id IS NULL
), state_mismatches AS (
    SELECT COUNT(*) AS n
    FROM games_chaintaskstate s
    JOIN games_task t ON t.id = s.task_id
    JOIN games_attempt a ON a.id = s.last_attempt_id
    LEFT JOIN group_game_map gm ON gm.task_group_id = t.task_group_id
    WHERE t.task_type IN ('wall', 'replacements_lines')
      AND (
          NOT (s.state <=> a.state)
          OR NOT (s.team_id <=> a.team_id)
          OR NOT (s.user_id <=> a.user_id)
          OR NOT (s.anon_key <=> a.anon_key)
          OR s.task_id <> a.task_id
          OR s.game_id <> COALESCE(a.game_id, gm.game_id)
          OR NOT (s.replay_slot_id <=> a.replay_slot_id)
      )
), duplicate_keys AS (
    SELECT COUNT(*) AS n FROM (
        SELECT s.actor_key, s.task_id, s.game_id, s.game_mode, s.replay_slot_key
        FROM games_chaintaskstate s
        JOIN games_task t ON t.id = s.task_id
        WHERE t.task_type IN ('wall', 'replacements_lines')
        GROUP BY s.actor_key, s.task_id, s.game_id, s.game_mode, s.replay_slot_key
        HAVING COUNT(*) > 1
    ) duplicates
)
SELECT
    (SELECT COUNT(*) FROM expected_rows) AS expected_rows,
    (SELECT n FROM missing) AS missing_rows,
    (SELECT n FROM extras) AS extra_rows,
    (SELECT n FROM unresolved) AS unresolved_attempts,
    (SELECT n FROM invalid_actors) AS invalid_actor_contexts,
    (SELECT n FROM state_mismatches) AS last_attempt_state_mismatches,
    (SELECT n FROM duplicate_keys) AS duplicate_keys
"""


class Command(BaseCommand):
    help = 'Read-only audit of ChainTaskState backfill key coverage.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--execute', action='store_true',
            help='Run the full read-only aggregate scan; inspect EXPLAIN first.',
        )
        parser.add_argument(
            '--max-execution-ms', type=int, default=3000,
            help='MySQL statement time limit for execution mode (default: 3000 ms).',
        )

    def handle(self, *args, **options):
        if connection.vendor != 'mysql':
            raise CommandError('This audit requires MySQL 8; no changes are made.')
        if options['max_execution_ms'] < 1 or options['max_execution_ms'] > 10000:
            raise CommandError('--max-execution-ms must be between 1 and 10000.')

        try:
            with connection.cursor() as cursor:
                if not options['execute']:
                    cursor.execute('EXPLAIN FORMAT=JSON ' + AUDIT_SQL)
                    import json

                    plan = json.loads(cursor.fetchone()[0])
                    scans = []

                    def visit(node):
                        if isinstance(node, dict):
                            if 'table_name' in node:
                                scans.append({
                                    'table': node.get('table_name'),
                                    'access': node.get('access_type'),
                                    'estimated_rows': node.get('rows_examined_per_scan'),
                                    'estimated_bytes': node.get('data_read_per_join'),
                                    'key': node.get('key'),
                                    'possible_keys': node.get('possible_keys'),
                                })
                            for flag in ('using_temporary_table', 'using_filesort'):
                                if flag in node:
                                    operators.append({flag: node[flag]})
                            for value in node.values():
                                visit(value)
                        elif isinstance(node, list):
                            for value in node:
                                visit(value)

                    operators = []
                    visit(plan)
                    self.stdout.write(json.dumps({'scans': scans}, sort_keys=True))
                    self.stdout.write(json.dumps({'operators': operators}, sort_keys=True))
                    self.stdout.write('EXPLAIN only; no completeness verdict was calculated.')
                    return
                prefix, final_select = AUDIT_SQL.rsplit('\nSELECT\n', 1)
                sql = '{}\nSELECT /*+ MAX_EXECUTION_TIME({}) */\n{}'.format(
                    prefix, options['max_execution_ms'], final_select,
                )
                cursor.execute(sql)
                row = cursor.fetchone()
        except DatabaseError as exc:
            raise CommandError(
                'Read-only audit did not complete; treat the result as INCONCLUSIVE. '
                'No writes were issued. {}'.format(type(exc).__name__)
            ) from exc

        names = [
            'expected_rows', 'missing_rows', 'extra_rows', 'unresolved_attempts',
            'invalid_actor_contexts', 'last_attempt_state_mismatches', 'duplicate_keys',
        ]
        result = dict(zip(names, row))
        for name, value in result.items():
            self.stdout.write('{}={}'.format(name, value))
        complete = (
            result['missing_rows'] == 0
            and result['extra_rows'] == 0
            and result['unresolved_attempts'] == 0
            and result['invalid_actor_contexts'] == 0
            and result['last_attempt_state_mismatches'] == 0
            and result['duplicate_keys'] == 0
        )
        self.stdout.write('verdict={}'.format('COMPLETE' if complete else 'INCOMPLETE'))
