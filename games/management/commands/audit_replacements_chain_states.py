"""Read-only audit for the replacements_lines ChainTaskState migration."""

import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from games.check import CheckerFactory
from games.models import Attempt, ChainTaskState, CheckerType, Game, GameTaskGroup, Task


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
    help = 'Read-only audit of replacements_lines ChainTaskState migration candidates.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true', required=True,
            help='Required safety flag; this command never writes data.',
        )
        parser.add_argument('--task-id', type=int)
        parser.add_argument('--game-id')
        parser.add_argument('--limit', type=int)
        parser.add_argument('--json', action='store_true', dest='json_output')

    def handle(self, *args, **options):
        if not options['dry_run']:
            raise CommandError('Read-only audit requires --dry-run.')

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
        for key in ordered_keys:
            item = self._audit_key(key)
            if item['state_mismatch'] or item['attempt_mismatch'] or item['errors']:
                items.append(item)

        summary = {
            'candidate_count': len(ordered_keys),
            'suspect_count': len(items),
            'state_mismatch_count': sum(item['state_mismatch'] for item in items),
            'attempt_mismatch_count': sum(item['attempt_mismatch'] for item in items),
            'error_count': sum(bool(item['errors']) for item in items),
        }
        if options['json_output']:
            self.stdout.write(json.dumps(
                {'summary': summary, 'items': items},
                ensure_ascii=False, sort_keys=True,
            ))
        else:
            self.stdout.write('summary={}'.format(json.dumps(summary, ensure_ascii=False)))
            for item in items:
                self.stdout.write('suspect={}'.format(json.dumps(item, ensure_ascii=False, sort_keys=True)))

    def _audit_key(self, key):
        task_id, game_id, team_id, user_id, anon_key, replay_slot_id = key
        task = Task.objects.get(pk=task_id)
        if game_id:
            game = Game.objects.get(pk=game_id)
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
        actor = _actor_filter(team_id, user_id, anon_key)
        attempts = list(Attempt.manager.filter(
            task_id=task_id, game_id=game_id, replay_slot_id=replay_slot_id, **actor,
        ).order_by('time', 'pk'))
        rows = {
            row.game_mode: row
            for row in ChainTaskState.objects.filter(
                task_id=task_id, game_id=game_id, replay_slot_id=replay_slot_id, **actor,
            )
        }
        states = {'general': None, 'tournament': None}
        attempt_mismatch = 0
        errors = []
        checker_type = CheckerType.objects.get(pk='replacements_lines')

        for attempt in attempts:
            mode = game.get_current_mode(attempt)
            try:
                checker = CheckerFactory().create_checker(
                    checker_type, task.checker_data or '', states[mode],
                )
                result = checker.check(attempt.text, attempt)
                expected_status = result.status
                expected_points = Decimal(str(result.points or 0)) * task.get_points()
                if (
                    attempt.status != expected_status
                    or Decimal(str(attempt.points or 0)) != expected_points
                    or not _json_equal(attempt.state, result.state)
                    or attempt.skip
                ):
                    attempt_mismatch += 1
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
            'errors': errors,
        }
