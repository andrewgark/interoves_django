"""Read-only audit for the replacements_lines ChainTaskState migration."""

import json
from collections import defaultdict
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from games.check import CheckerFactory
from games.models import (
    Attempt, ChainTaskState, CheckerType, Game, GameTaskGroup, ReplaySlot, Task, Team,
)
from games.recheck import recheck_chain_task


def _actor_filter(team_id, user_id, anon_key):
    return {
        'team_id': team_id,
        'user_id': user_id,
        'anon_key': anon_key,
    }


def _json_equal(left, right):
    if left in (None, '') and right in (None, ''):
        return True
    try:
        return json.loads(left or 'null') == json.loads(right or 'null')
    except (TypeError, ValueError):
        return left == right


class Command(BaseCommand):
    help = 'Audit or explicitly apply the replacements_lines ChainTaskState migration.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Read-only mode.')
        parser.add_argument('--apply', action='store_true', help='Apply the audited rechecks.')
        parser.add_argument(
            '--confirm', default='',
            help='Required with --apply: REPLACEMENTS_STATE_MIGRATION.',
        )
        parser.add_argument('--task-id', type=int)
        parser.add_argument('--game-id')
        parser.add_argument('--limit', type=int)
        parser.add_argument('--batch-size', type=int, default=100)
        parser.add_argument('--json', action='store_true', dest='json_output')
        parser.add_argument('--summary-only', action='store_true')

    def handle(self, *args, **options):
        if options['apply'] and options['dry_run']:
            raise CommandError('--apply and --dry-run are mutually exclusive.')
        if not options['apply'] and not options['dry_run']:
            raise CommandError('Choose --dry-run or --apply.')
        if options['apply'] and options['confirm'] != 'REPLACEMENTS_STATE_MIGRATION':
            raise CommandError('--apply requires --confirm REPLACEMENTS_STATE_MIGRATION.')

        keys = set()
        attempt_keys = Attempt.manager.filter(
            task__task_type='replacements_lines',
        )
        if options.get('task_id'):
            attempt_keys = attempt_keys.filter(task_id=options['task_id'])
        if options.get('game_id'):
            attempt_keys = attempt_keys.filter(game_id=options['game_id'])
        keys.update(attempt_keys.values_list(
            'task_id', 'game_id', 'team_id', 'user_id', 'anon_key', 'replay_slot_id',
        ).distinct())

        state_keys = ChainTaskState.objects.filter(
            task__task_type='replacements_lines',
        )
        if options.get('task_id'):
            state_keys = state_keys.filter(task_id=options['task_id'])
        if options.get('game_id'):
            state_keys = state_keys.filter(game_id=options['game_id'])
        keys.update(state_keys.values_list(
            'task_id', 'game_id', 'team_id', 'user_id', 'anon_key', 'replay_slot_id',
        ).distinct())

        ordered_keys = sorted(
            keys,
            key=lambda value: tuple('' if item is None else str(item) for item in value),
        )
        if options.get('limit') is not None:
            ordered_keys = ordered_keys[:max(0, options['limit'])]

        items = []
        batch_size = max(1, options['batch_size'])
        checker_type = CheckerType.objects.get(pk='replacements_lines')
        for offset in range(0, len(ordered_keys), batch_size):
            batch = ordered_keys[offset:offset + batch_size]
            task_ids = {key[0] for key in batch}
            game_ids = {key[1] for key in batch if key[1] is not None}
            tasks = Task.objects.in_bulk(task_ids)
            games = Game.objects.in_bulk(game_ids)
            attempts_by_key = defaultdict(list)
            for attempt in Attempt.manager.filter(
                task_id__in=task_ids,
                game_id__in=game_ids,
            ).order_by('time', 'pk'):
                attempts_by_key[self._key_for_row(attempt)].append(attempt)
            states_by_key = defaultdict(dict)
            for row in ChainTaskState.objects.filter(
                task_id__in=task_ids,
                game_id__in=game_ids,
            ):
                states_by_key[self._key_for_row(row)][row.game_mode] = row
            for key in batch:
                item = self._audit_key(
                    key,
                    task=tasks.get(key[0]),
                    game=games.get(key[1]),
                    attempts=attempts_by_key.get(key, []),
                    rows=states_by_key.get(key, {}),
                    checker_type=checker_type,
                )
                if item['state_mismatch'] or item['attempt_mismatch'] or item['errors']:
                    items.append(item)

        summary = {
            'candidate_count': len(ordered_keys),
            'suspect_count': len(items),
            'state_mismatch_count': sum(item['state_mismatch'] for item in items),
            'attempt_mismatch_count': sum(item['attempt_mismatch'] for item in items),
            'error_count': sum(bool(item['errors']) for item in items),
        }
        if options['apply']:
            applied = 0
            apply_errors = []
            for item in items:
                if item['errors']:
                    continue
                try:
                    self._apply_item(item)
                    applied += 1
                except Exception as exc:
                    apply_errors.append('{}: {}'.format(item['task_id'], exc))
            summary['applied_count'] = applied
            summary['apply_error_count'] = len(apply_errors)
            summary['apply_errors'] = apply_errors
        if options['json_output']:
            self.stdout.write(json.dumps(
                {
                    'summary': summary,
                    'items': [] if options['summary_only'] else items,
                },
                ensure_ascii=False, sort_keys=True,
            ))
        else:
            self.stdout.write('summary={}'.format(json.dumps(summary, ensure_ascii=False)))
            if not options['summary_only']:
                for item in items:
                    self.stdout.write('suspect={}'.format(json.dumps(item, ensure_ascii=False, sort_keys=True)))

    def _apply_item(self, item):
        task = Task.objects.get(pk=item['task_id'])
        game = Game.objects.get(pk=item['game_id'])
        team = Team.objects.filter(pk=item['team_id']).first() if item['team_id'] else None
        user_model = get_user_model()
        user = user_model.objects.filter(pk=item['user_id']).first() if item['user_id'] else None
        replay_slot = (
            ReplaySlot.objects.filter(pk=item['replay_slot_id']).first()
            if item['replay_slot_id'] else None
        )
        recheck_chain_task(
            task=task,
            team=team,
            user=user,
            anon_key=item['anon_key'],
            game=game,
            replay_slot=replay_slot,
            notify=False,
        )

    @staticmethod
    def _key_for_row(row):
        return (
            row.task_id, row.game_id, row.team_id, row.user_id,
            row.anon_key, row.replay_slot_id,
        )

    def _audit_key(self, key, *, task=None, game=None, attempts=None, rows=None, checker_type=None):
        task_id, game_id, team_id, user_id, anon_key, replay_slot_id = key
        task = task or Task.objects.get(pk=task_id)
        if game_id:
            game = game or Game.objects.get(pk=game_id)
        else:
            try:
                game = GameTaskGroup.resolve_game_for_task(task)
            except Exception as exc:
                return {
                    'task_id': task_id,
                    'game_id': None,
                    'team_id': team_id,
                    'user_id': user_id,
                    'anon_key': anon_key,
                    'replay_slot_id': replay_slot_id,
                    'attempt_count': 0,
                    'state_mismatch': 0,
                    'attempt_mismatch': 0,
                    'errors': ['cannot resolve game: {}'.format(exc)],
                }
            if game is None:
                return {
                    'task_id': task_id,
                    'game_id': None,
                    'team_id': team_id,
                    'user_id': user_id,
                    'anon_key': anon_key,
                    'replay_slot_id': replay_slot_id,
                    'attempt_count': 0,
                    'state_mismatch': 0,
                    'attempt_mismatch': 0,
                    'errors': ['cannot resolve game'],
                }
        attempts = list(attempts or [])
        rows = rows or {}
        states = {'general': None, 'tournament': None}
        attempt_mismatch = 0
        mismatched_attempts = []
        errors = []
        checker_type = checker_type or CheckerType.objects.get(pk='replacements_lines')

        for attempt in attempts:
            mode = game.get_current_mode(attempt)
            try:
                checker = CheckerFactory().create_checker(
                    checker_type, task.checker_data or '', states[mode],
                )
                result = checker.check(attempt.text, attempt)
                expected_status = result.status
                expected_points = Decimal(str(result.points or 0)) * task.get_points()
                mismatch = (
                    attempt.status != expected_status
                    or Decimal(str(attempt.points or 0)) != expected_points
                    or not _json_equal(attempt.state, result.state)
                    or attempt.skip
                )
                if mismatch:
                    attempt_mismatch += 1
                    mismatched_attempts.append({
                        'id': attempt.pk,
                        'mode': mode,
                        'status': attempt.status,
                        'expected_status': expected_status,
                        'points': str(attempt.points or 0),
                        'expected_points': str(expected_points),
                        'state_matches': _json_equal(attempt.state, result.state),
                        'skip': attempt.skip,
                    })
                states[mode] = result.state
            except Exception as exc:  # report bad historical rows, never abort the audit
                errors.append('attempt {}: {}'.format(attempt.pk, exc))

        state_mismatch = 0
        for mode in ('general', 'tournament'):
            row = rows.get(mode)
            if row is None:
                if states[mode] is not None:
                    state_mismatch += 1
            elif not _json_equal(row.state, states[mode]):
                state_mismatch += 1

        return {
            'task_id': task_id,
            'game_id': game_id,
            'team_id': team_id,
            'user_id': user_id,
            'anon_key': anon_key,
            'replay_slot_id': replay_slot_id,
            'attempt_count': len(attempts),
            'state_mismatch': state_mismatch,
            'attempt_mismatch': attempt_mismatch,
            'mismatched_attempts': mismatched_attempts,
            'calculated_states': states,
            'stored_states': {
                mode: (row.state if row else None)
                for mode, row in rows.items()
            },
            'errors': errors,
        }
