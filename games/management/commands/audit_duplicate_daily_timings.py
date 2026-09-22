"""Read-only forensic export for duplicate first-play DailySolveTiming rows.

This command intentionally uses only columns shared by the pre-Team and
Team-aware schemas so it can audit production before migration 0217.
Anonymous identities are represented by one-way fingerprints only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection

from games.models import (
    AnonAccountClaim, Attempt, DailySolveTiming, GameTaskGroup, HintAttempt,
    PlayerCompletedGame, PlayerStartedGame,
)
from games.share_result import elapsed_seconds_from_attempts


TIMING_FIELDS = (
    'id', 'user_id', 'team_id', 'anon_key', 'game_id', 'task_group_id',
    'replay_slot_id', 'timing_version', 'status', 'accumulated_ms', 'frozen_ms',
    'interval_started_at', 'last_heartbeat_at', 'completed_at', 'created_at',
    'updated_at', 'last_seq', 'last_event_id', 'active_session_id',
)


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'hex') and value.__class__.__name__ == 'UUID':
        return str(value)
    return value


def _fingerprint(value):
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:16]


def _actor_from_row(row):
    for kind, field in (('team', 'team_id'), ('profile', 'user_id'), ('anonymous', 'anon_key')):
        value = row.get(field)
        if value is not None:
            return kind, value
    return 'invalid', None


def _actor_filter(kind, value):
    if kind == 'team':
        return {'team_id': value, 'user__isnull': True, 'anon_key__isnull': True}
    if kind == 'profile':
        return {'user_id': value, 'team__isnull': True, 'anon_key__isnull': True}
    return {'anon_key': value, 'user__isnull': True, 'team__isnull': True}


class Command(BaseCommand):
    help = 'Read-only forensic report of duplicate canonical daily timing rows.'

    def add_arguments(self, parser):
        parser.add_argument('--format', choices=('text', 'jsonl', 'json'), default='text')

    def handle(self, *args, **options):
        table = connection.ops.quote_name(DailySolveTiming._meta.db_table)
        with connection.cursor() as cursor:
            available = {
                column.name for column in connection.introspection.get_table_description(
                    cursor, DailySolveTiming._meta.db_table,
                )
            }
        identity_fields = [name for name in ('team_id', 'user_id', 'anon_key') if name in available]
        group_fields = ['game_id', 'task_group_id', *identity_fields]
        select = ', '.join(connection.ops.quote_name(name) for name in group_fields)
        where = connection.ops.quote_name('replay_slot_id') + ' IS NULL'
        sql = (
            f'SELECT {select}, COUNT(*) AS duplicate_count FROM {table} '
            f'WHERE {where} GROUP BY {select} HAVING COUNT(*) > 1 '
            f'ORDER BY {connection.ops.quote_name("game_id")}, '
            f'{connection.ops.quote_name("task_group_id")}'
        )
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [column[0] for column in cursor.description]
            groups = [dict(zip(columns, values)) for values in cursor.fetchall()]

        report = [self._build_group(group, available) for group in groups]
        if options['format'] == 'json':
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=_json_value))
        elif options['format'] == 'jsonl':
            for row in report:
                self.stdout.write(json.dumps(row, ensure_ascii=False, default=_json_value))
        else:
            self.stdout.write('duplicate_groups={}'.format(len(report)))
            for row in report:
                values = ','.join('{}:{}'.format(t['id'], t['canonical_elapsed_seconds']) for t in row['timings'])
                self.stdout.write(
                    '{} game={} release={} actor={} rows={} elapsed_s={}'.format(
                        row['group_id'], row['game_id'], row['release']['number'],
                        row['actor']['type'], ','.join(str(t['id']) for t in row['timings']), values,
                    )
                )

    def _build_group(self, group, available):
        kind, actor_id = _actor_from_row(group)
        identity = _actor_filter(kind, actor_id)
        game_id = group['game_id']
        task_group_id = group['task_group_id']
        timing_fields = [name for name in TIMING_FIELDS if name in available]
        selected_actor_field = {
            'team': 'team_id', 'profile': 'user_id', 'anonymous': 'anon_key',
        }[kind]
        attempt_filter = {
            'game_id': game_id, 'task__task_group_id': task_group_id,
            'replay_slot__isnull': True, **identity,
        }
        attempts_qs = Attempt.manager.filter(**attempt_filter).order_by('time', 'pk')
        attempts = list(attempts_qs.values(
            'id', 'task_id', 'time', 'status', 'skip', 'points', 'active_time_ms', 'replay_slot_id',
        ))
        attempts_for_elapsed = list(attempts_qs.only(
            'id', 'task_id', 'game_id', 'user_id', 'team_id', 'anon_key', 'replay_slot_id',
            'time', 'status', 'skip', 'points', 'active_time_ms',
        ))
        replay_attempts_for_elapsed = list(Attempt.manager.filter(
            game_id=game_id, task__task_group_id=task_group_id,
            replay_slot__isnull=False, **identity,
        ).only(
            'id', 'task_id', 'game_id', 'user_id', 'team_id', 'anon_key', 'replay_slot_id',
            'time', 'status', 'skip', 'points', 'active_time_ms',
        ))
        timing_table = connection.ops.quote_name(DailySolveTiming._meta.db_table)
        selected_fields = ', '.join(connection.ops.quote_name(name) for name in timing_fields)
        timing_rows = self._select_timing_rows(
            timing_table, selected_fields, timing_fields, available,
            game_id, task_group_id, selected_actor_field, actor_id,
            replay=False,
            attempts=attempts_for_elapsed,
        )
        replay_timing_rows = self._select_timing_rows(
            timing_table, selected_fields, timing_fields, available,
            game_id, task_group_id, selected_actor_field, actor_id,
            replay=True, attempts=replay_attempts_for_elapsed,
        )

        selected = timing_rows[0]  # Django QuerySet.first() on an unordered query orders by PK.
        release = GameTaskGroup.objects.filter(
            game_id=game_id, task_group_id=task_group_id,
        ).order_by('pk').values('number', 'name').first() or {}
        actor_label = (
            {'type': kind, 'id': actor_id} if kind != 'anonymous'
            else {'type': kind, 'fingerprint': _fingerprint(actor_id)}
        )
        replay_attempts = list(Attempt.manager.filter(
            game_id=game_id, task__task_group_id=task_group_id,
            replay_slot__isnull=False, **identity,
        ).order_by('time', 'pk').values('id', 'task_id', 'time', 'status', 'replay_slot_id'))
        hint_filter = {'hint__task__task_group_id': task_group_id, **identity}
        hints = list(HintAttempt.objects.filter(**hint_filter).order_by('time', 'pk').values(
            'id', 'hint_id', 'time', 'is_real_request', 'replay_slot_id',
        ))
        completions = list(PlayerCompletedGame.objects.filter(
            game_id=game_id, task_group_id=task_group_id, **identity,
        ).order_by('completed_at', 'pk').values(
            'id', 'game_kind', 'game_instance_id', 'public_game_id', 'result',
            'completed_at', 'is_backfilled',
        ))
        starts = list(PlayerStartedGame.objects.filter(
            game_id=game_id, task_group_id=task_group_id, **identity,
        ).order_by('started_at', 'pk').values(
            'id', 'game_kind', 'game_instance_id', 'public_game_id',
            'started_at', 'is_backfilled',
        ))
        if kind == 'anonymous':
            claim_rows = list(AnonAccountClaim.objects.filter(anon_key=actor_id).values(
                'user_id', 'anon_key', 'created_at',
            ))
            claims = [{'user_id': row['user_id'], 'created_at': row['created_at']} for row in claim_rows]
            claimed_user_ids = [row['user_id'] for row in claim_rows]
            claimed_anon_keys = []
        elif kind == 'profile':
            claim_rows = list(AnonAccountClaim.objects.filter(user_id=actor_id).values(
                'anon_key', 'created_at',
            ))
            claims = [{
                'anon_key_fingerprint': _fingerprint(row['anon_key']),
                'created_at': row['created_at'],
            } for row in claim_rows]
            claimed_user_ids = []
            claimed_anon_keys = [row['anon_key'] for row in claim_rows]
        else:
            claims = []
            claimed_user_ids = []
            claimed_anon_keys = []
        claimed_actor_timings = self._claimed_actor_timings(
            timing_table, timing_fields, available, game_id, task_group_id,
            claimed_user_ids=claimed_user_ids,
            claimed_anon_keys=claimed_anon_keys,
        )
        for collection in (attempts, replay_attempts, hints, completions, starts):
            for item in collection:
                if 'game_instance_id' in item:
                    item['game_instance_id_fingerprint'] = _fingerprint(item.pop('game_instance_id'))
                if 'public_game_id' in item and item['public_game_id']:
                    item['public_game_id_fingerprint'] = _fingerprint(item.pop('public_game_id'))
                for key, value in list(item.items()):
                    item[key] = _json_value(value)
        for claim in claims:
            for key, value in list(claim.items()):
                claim[key] = _json_value(value)

        return {
            'group_id': _fingerprint('{}|{}|{}|{}'.format(game_id, task_group_id, kind, actor_id)),
            'game_id': game_id,
            'release': release,
            'task_group_id': task_group_id,
            'actor': actor_label,
            'production_selector': 'QuerySet.first(); unordered query uses ascending primary key',
            'production_selected_row_id': selected['id'],
            'production_selected_elapsed_seconds': selected['canonical_elapsed_seconds'],
            'timings': timing_rows,
            'replay_timings': replay_timing_rows,
            'attempts_first_play': attempts,
            'attempts_replay': replay_attempts,
            'hint_attempts': hints,
            'start_events': starts,
            'completion_events': completions,
            'anon_account_claims': claims,
            'claimed_actor_timings': claimed_actor_timings,
        }

    def _claimed_actor_timings(
        self, table, timing_fields, available, game_id, task_group_id,
        *, claimed_user_ids, claimed_anon_keys,
    ):
        identities = []
        if claimed_user_ids and 'user_id' in available:
            identities.append(('profile', 'user_id', claimed_user_ids))
        if claimed_anon_keys and 'anon_key' in available:
            identities.append(('anonymous', 'anon_key', claimed_anon_keys))
        if not identities:
            return []
        selected_fields = ', '.join(connection.ops.quote_name(name) for name in timing_fields)
        result = []
        for kind, field, values in identities:
            params = [game_id, task_group_id, *values]
            placeholders = ', '.join(['%s'] * len(values))
            clauses = [
                connection.ops.quote_name('game_id') + ' = %s',
                connection.ops.quote_name('task_group_id') + ' = %s',
                connection.ops.quote_name('replay_slot_id') + ' IS NULL',
                connection.ops.quote_name(field) + ' IN ({})'.format(placeholders),
            ]
            for other in ('team_id', 'user_id', 'anon_key'):
                if other in available and other != field:
                    clauses.append(connection.ops.quote_name(other) + ' IS NULL')
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT {} FROM {} WHERE {} ORDER BY {}'.format(
                        selected_fields, table, ' AND '.join(clauses),
                        connection.ops.quote_name('id'),
                    ),
                    params,
                )
                rows = [
                    dict(zip([column[0] for column in cursor.description], values))
                    for values in cursor.fetchall()
                ]
            for row in rows:
                row['actor_type'] = kind
                if row.get('anon_key') is not None:
                    row['actor_fingerprint'] = _fingerprint(row.pop('anon_key'))
                for key, value in list(row.items()):
                    row[key] = _json_value(value)
            result.extend(rows)
        return result

    def _select_timing_rows(
        self, table, selected_fields, timing_fields, available, game_id,
        task_group_id, selected_actor_field, actor_id, *, replay, attempts,
    ):
        where = [
            connection.ops.quote_name('game_id') + ' = %s',
            connection.ops.quote_name('task_group_id') + ' = %s',
            connection.ops.quote_name('replay_slot_id') + (' IS NOT NULL' if replay else ' IS NULL'),
        ]
        params = [game_id, task_group_id]
        for field in ('team_id', 'user_id', 'anon_key'):
            if field not in available:
                continue
            quoted = connection.ops.quote_name(field)
            if field == selected_actor_field:
                where.append(quoted + ' = %s')
                params.append(actor_id)
            else:
                where.append(quoted + ' IS NULL')
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT {} FROM {} WHERE {} ORDER BY {}'.format(
                    selected_fields, table, ' AND '.join(where),
                    connection.ops.quote_name('id'),
                ),
                params,
            )
            rows = [
                dict(zip([column[0] for column in cursor.description], values))
                for values in cursor.fetchall()
            ]
        for row in rows:
            row['canonical_elapsed_seconds'] = self._production_elapsed_seconds(
                row, attempts, replay_slot=row.get('replay_slot_id'),
            )
            row['active_session_present'] = bool(row.get('active_session_id'))
            if row.get('active_session_id'):
                row['active_session_fingerprint'] = _fingerprint(row['active_session_id'])
            row.pop('active_session_id', None)
            if row.get('anon_key') is not None:
                row['anon_key_fingerprint'] = _fingerprint(row.pop('anon_key'))
            for key, value in list(row.items()):
                row[key] = _json_value(value)
        return rows

    @staticmethod
    def _production_elapsed_seconds(row, attempts, *, replay_slot):
        """Mirror the deployed ab34888 helper, including legacy fallback."""
        if int(row.get('timing_version') or 0) < DailySolveTiming.TIMING_VERSION_ACTIVE:
            scoped_attempts = [a for a in attempts if a.replay_slot_id == replay_slot]
            return elapsed_seconds_from_attempts(scoped_attempts)
        if row.get('status') == DailySolveTiming.STATUS_COMPLETED and row.get('frozen_ms') is not None:
            return max(0, int(row['frozen_ms']) // 1000)
        return max(0, int(row.get('accumulated_ms') or 0) // 1000)
