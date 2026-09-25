"""Plan and (only with an explicit plan) clean duplicate first-play timings."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from games.models import (
    Attempt,
    DailySolveTiming,
    GameTaskGroup,
    PlayerCompletedGame,
    PlayerStartedGame,
)


PLAN_VERSION = 1
RACE_WINDOW = timedelta(seconds=1)


def _json_value(value):
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value) if value is not None else None


def _actor_type(group):
    present = [
        ('team', group.get('team_id')),
        ('user', group.get('user_id')),
        ('anon', group.get('anon_key')),
    ]
    selected = [(kind, value) for kind, value in present if value is not None]
    return selected[0] if len(selected) == 1 else ('invalid', None)


def _identity_filter(group):
    return {
        field: group.get(field)
        for field in ('team_id', 'user_id', 'anon_key')
    }


def _row_signature(row):
    values = {
        'id': row.id,
        'game_id': row.game_id,
        'task_group_id': row.task_group_id,
        'team_id': row.team_id,
        'user_id': row.user_id,
        'anon_key': row.anon_key,
        'replay_slot_id': row.replay_slot_id,
        'status': row.status,
        'accumulated_ms': row.accumulated_ms,
        'frozen_ms': row.frozen_ms,
        'interval_started_at': _json_value(row.interval_started_at),
        'last_heartbeat_at': _json_value(row.last_heartbeat_at),
        'completed_at': _json_value(row.completed_at),
        'created_at': _json_value(row.created_at),
        'updated_at': _json_value(row.updated_at),
        'last_seq': row.last_seq,
        'last_event_id': row.last_event_id,
        'active_session_id': _json_value(row.active_session_id),
        'applied_event_ids': row.applied_event_ids or [],
    }
    encoded = json.dumps(values, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _row_report(row):
    return {
        'id': row.id,
        'status': row.status,
        'replay_slot_id': row.replay_slot_id,
        'created_at': _json_value(row.created_at),
        'updated_at': _json_value(row.updated_at),
        'started_at': _json_value(row.interval_started_at),
        'last_heartbeat_at': _json_value(row.last_heartbeat_at),
        'completed_at': _json_value(row.completed_at),
        'accumulated_ms': row.accumulated_ms,
        'frozen_ms': row.frozen_ms,
        'last_seq': row.last_seq,
        'last_event_id': row.last_event_id,
        'active_session': bool(row.active_session_id),
        'applied_event_count': len(row.applied_event_ids or []),
        'signature': _row_signature(row),
    }


def _assessment(group, rows):
    actor_kind, actor_id = _actor_type(group)
    identity = _identity_filter(group)
    game_id, task_group_id = group['game_id'], group['task_group_id']
    attempts = list(Attempt.manager.filter(
        game_id=game_id, task__task_group_id=task_group_id,
        replay_slot__isnull=True, **identity,
    ).order_by('time', 'pk').values('id', 'time', 'status', 'points', 'active_time_ms'))
    completions = list(PlayerCompletedGame.objects.filter(
        game_id=game_id, task_group_id=task_group_id, **identity,
    ).order_by('completed_at', 'pk').values('id', 'result', 'completed_at'))
    starts = list(PlayerStartedGame.objects.filter(
        game_id=game_id, task_group_id=task_group_id, **identity,
    ).order_by('started_at', 'pk').values('id', 'started_at'))
    completed = next((row for row in rows if row.status == DailySolveTiming.STATUS_COMPLETED), None)
    running = next((row for row in rows if row.status == DailySolveTiming.STATUS_RUNNING), None)
    evidence = []
    classification = 'AMBIGUOUS'
    keep = delete = None
    if completed is not None and running is not None:
        completed_result = any(
            item['result'] == PlayerCompletedGame.RESULT_SOLVED for item in completions
        )
        has_ok_attempt = any(item['status'] == 'Ok' for item in attempts)
        race_created = abs(completed.created_at - running.created_at) <= RACE_WINDOW
        orphan_running = (
            running.accumulated_ms == 0
            and running.frozen_ms is None
            and running.completed_at is None
            and int(running.last_seq or 0) <= 2
            and len(running.applied_event_ids or []) <= 1
            and running.updated_at - running.created_at <= RACE_WINDOW
        )
        authoritative_completed = (
            completed.frozen_ms is not None
            and completed.completed_at is not None
            and (completed_result or has_ok_attempt)
        )
        if race_created and orphan_running and authoritative_completed:
            classification = 'SAFE_DELETE'
            keep, delete = completed, running
            evidence = [
                'completed timing has frozen_ms and completed_at',
                'solved completion or Ok attempt exists for the logical game',
                'running timing was created in the same race window',
                'running timing has zero duration and only its initial event',
            ]
    release = GameTaskGroup.objects.filter(
        game_id=game_id, task_group_id=task_group_id,
    ).order_by('pk').values('number', 'name').first() or {}
    actor = {
        'type': actor_kind,
        'id': actor_id if actor_kind != 'anon' else hashlib.sha256(str(actor_id).encode()).hexdigest()[:16],
    }
    item = {
        'game_id': game_id,
        'task_group_id': task_group_id,
        'release': release,
        'actor': actor,
        'classification': classification,
        'evidence': evidence,
        'timings': [_row_report(row) for row in rows],
        'attempt_count': len(attempts),
        'attempt_points': [str(item['points']) for item in attempts],
        'completion_rows': [
            {'id': item['id'], 'result': item['result'], 'completed_at': _json_value(item['completed_at'])}
            for item in completions
        ],
        'start_rows': [
            {'id': item['id'], 'started_at': _json_value(item['started_at'])}
            for item in starts
        ],
    }
    if keep is not None:
        item['keep_id'] = keep.id
        item['delete_id'] = delete.id
        item['keep_signature'] = _row_signature(keep)
        item['delete_signature'] = _row_signature(delete)
    return item


def build_plan():
    groups = DailySolveTiming.objects.filter(replay_slot__isnull=True).values(
        'game_id', 'task_group_id', 'team_id', 'user_id', 'anon_key',
    ).annotate(row_count=Count('id')).filter(row_count__gt=1).order_by(
        'game_id', 'task_group_id', 'team_id', 'user_id', 'anon_key',
    )
    report = []
    for group in groups:
        rows = list(DailySolveTiming.objects.filter(
            game_id=group['game_id'], task_group_id=group['task_group_id'],
            replay_slot__isnull=True, **_identity_filter(group),
        ).order_by('id'))
        report.append(_assessment(group, rows))
    return {
        'plan_version': PLAN_VERSION,
        'generated_at': timezone.now().isoformat(),
        'logical_key': 'game + task_group + actor(user/team/anon) + replay_slot=NULL',
        'duplicate_groups': report,
        'planned_deletes': [
            {
                'game_id': item['game_id'],
                'task_group_id': item['task_group_id'],
                'keep_id': item['keep_id'],
                'delete_id': item['delete_id'],
                'keep_signature': item['keep_signature'],
                'delete_signature': item['delete_signature'],
                'classification': item['classification'],
            }
            for item in report if item['classification'] == 'SAFE_DELETE'
        ],
    }


def _write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_value) + '\n')


class Command(BaseCommand):
    help = 'Dry-run or apply an explicitly audited cleanup of duplicate first-play timings.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--plan-file')
        parser.add_argument('--write-plan')
        parser.add_argument('--format', choices=('text', 'json'), default='text')

    def handle(self, *args, **options):
        if options['apply'] and not options['plan_file']:
            raise CommandError('--apply requires --plan-file from a prior dry-run')
        if options['apply']:
            return self._apply(options['plan_file'], options['format'])
        plan = build_plan()
        if options['write_plan']:
            _write_json(options['write_plan'], plan)
        if options['format'] == 'json':
            self.stdout.write(json.dumps(plan, ensure_ascii=False, indent=2, default=_json_value))
        else:
            self._print_text(plan, applied=False)

    def _apply(self, plan_file, output_format):
        try:
            plan = json.loads(Path(plan_file).read_text())
        except (OSError, ValueError) as exc:
            raise CommandError('cannot read plan file: {}'.format(exc)) from exc
        if plan.get('plan_version') != PLAN_VERSION:
            raise CommandError('unsupported plan_version')
        deleted, skipped = [], []
        with transaction.atomic():
            for item in plan.get('planned_deletes', []):
                delete = DailySolveTiming.objects.select_for_update().filter(pk=item['delete_id']).first()
                keep = DailySolveTiming.objects.select_for_update().filter(pk=item['keep_id']).first()
                valid = (
                    delete is not None and keep is not None
                    and delete.replay_slot_id is None and keep.replay_slot_id is None
                    and delete.game_id == item['game_id'] == keep.game_id
                    and delete.task_group_id == item['task_group_id'] == keep.task_group_id
                    and _row_signature(delete) == item['delete_signature']
                    and _row_signature(keep) == item['keep_signature']
                )
                if valid:
                    # Re-run the forensic classifier while rows are locked.
                    group = {
                        'game_id': delete.game_id, 'task_group_id': delete.task_group_id,
                        'team_id': delete.team_id, 'user_id': delete.user_id,
                        'anon_key': delete.anon_key,
                    }
                    current = _assessment(group, [keep, delete])
                    valid = (
                        current['classification'] == 'SAFE_DELETE'
                        and current.get('delete_id') == delete.id
                        and current.get('keep_id') == keep.id
                    )
                if not valid:
                    skipped.append(item['delete_id'])
                    continue
                delete.delete()
                deleted.append(item['delete_id'])
        result = {'deleted': deleted, 'skipped': skipped, 'deleted_count': len(deleted), 'skipped_count': len(skipped)}
        if output_format == 'json':
            self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            self.stdout.write('deleted={} skipped={}'.format(len(deleted), len(skipped)))
            self.stdout.write('deleted_ids={}'.format(','.join(str(value) for value in deleted) or 'none'))
            self.stdout.write('skipped_ids={}'.format(','.join(str(value) for value in skipped) or 'none'))

    def _print_text(self, plan, *, applied):
        groups = plan['duplicate_groups']
        counts = {}
        for item in groups:
            counts[item['classification']] = counts.get(item['classification'], 0) + 1
        self.stdout.write('duplicate_groups={}'.format(len(groups)))
        self.stdout.write('classifications={}'.format(json.dumps(counts, ensure_ascii=False, sort_keys=True)))
        self.stdout.write('planned_delete_rows={}'.format(len(plan['planned_deletes'])))
        for item in groups:
            ids = ','.join(str(row['id']) for row in item['timings'])
            self.stdout.write(
                '{} game={} release={} actor={} rows={} keep={} delete={} reason={}'.format(
                    item['classification'], item['game_id'], item['release'].get('number', '?'),
                    item['actor'].get('id'), ids, item.get('keep_id', '-'),
                    item.get('delete_id', '-'), '; '.join(item['evidence']) or 'conservative hold',
                )
            )
