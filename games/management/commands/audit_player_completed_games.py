import hashlib
import json
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from games.analytics import is_task_completion_state, is_task_group_complete
from games.models import Attempt, ChainTaskState, PlayerCompletedGame


CHAIN_TASK_TYPES = {'raddle', 'replacements_lines', 'alphabetty', 'word_salad'}


def _safe_actor(record):
    if record.team_id:
        return 'team', str(record.team_id)
    if record.user_id:
        return 'user', str(record.user_id)
    if record.anon_key:
        return 'anon', hashlib.sha256(record.anon_key.encode()).hexdigest()[:16]
    return 'unknown', 'unavailable'


def _game_category(game_id):
    return {
        'replacements': 'replacements',
        'ladder': 'raddle',
        'alphabetty': 'alphabetty',
        'salad': 'word_salad',
    }.get(game_id, 'other')


def _actor_filter(record):
    if record.team_id:
        return {'team_id': record.team_id, 'user__isnull': True, 'anon_key__isnull': True}
    if record.user_id:
        return {'user_id': record.user_id, 'team__isnull': True, 'anon_key__isnull': True}
    if record.anon_key:
        return {'anon_key': record.anon_key, 'team__isnull': True, 'user__isnull': True}
    return None


def _timestamp(value):
    return value.isoformat() if value is not None else None


class Command(BaseCommand):
    help = 'Read-only forensic audit of incomplete PlayerCompletedGame rows.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Required safety acknowledgement; never writes data.',
        )
        parser.add_argument('--id', dest='record_ids', action='append', type=int)
        parser.add_argument('--game', dest='game_id')

    def handle(self, *args, **options):
        if not options['dry_run']:
            raise CommandError('This command is read-only; pass --dry-run explicitly.')

        qs = PlayerCompletedGame.objects.select_related('game', 'task_group').order_by('pk')
        if options.get('record_ids'):
            qs = qs.filter(pk__in=options['record_ids'])
        if options.get('game_id'):
            qs = qs.filter(game_id=options['game_id'])

        summary = {
            'total_checked': 0,
            'valid': 0,
            'confirmed_invalid': 0,
            'ambiguous': 0,
            'breakdown': {},
        }
        for record in qs.iterator():
            item = self._audit_record(record)
            summary['total_checked'] += 1
            classification = item['classification']
            if classification == 'VALID':
                summary['valid'] += 1
            elif classification == 'CONFIRMED_INVALID':
                summary['confirmed_invalid'] += 1
            else:
                summary['ambiguous'] += 1
            game_summary = summary['breakdown'].setdefault(
                item['game_category'],
                {'suspect': 0, 'valid': 0, 'confirmed_invalid': 0, 'ambiguous': 0},
            )
            game_summary['suspect'] += 1
            game_summary[classification.lower()] += 1
            self.stdout.write(json.dumps(item, ensure_ascii=False, sort_keys=True))

        self.stdout.write(json.dumps({'summary': summary}, ensure_ascii=False, sort_keys=True))

    def _audit_record(self, record):
        actor = _actor_filter(record)
        tasks = list(record.task_group.tasks.visible().order_by('pk'))
        completed_current = []
        missing_current = []
        chain_tasks = []
        chain_completion_timestamps = {}
        attempt_success_timestamps = {}
        historical_missing_evidence = {}

        for task in tasks:
            if task.task_type in CHAIN_TASK_TYPES:
                chain_tasks.append({'id': task.id, 'type': task.task_type})
                states = ChainTaskState.objects.filter(
                    task=task,
                    game_id=record.game_id,
                    replay_slot__isnull=True,
                    **actor,
                ).order_by('updated_at')
                current_state = states.last()
                is_currently_complete = bool(
                    current_state and is_task_completion_state(task, current_state.state)
                )
                if is_currently_complete:
                    completed_current.append(task.id)
                    chain_completion_timestamps[str(task.id)] = [
                        _timestamp(state.updated_at)
                        for state in states
                        if is_task_completion_state(task, state.state)
                    ]
                else:
                    missing_current.append(task.id)
                    historical_missing_evidence[str(task.id)] = {
                        'successful_attempts_before_completion': [],
                        'completed_chain_states_before_completion': [],
                    }
                continue

            attempts = Attempt.objects.filter(
                task=task,
                game_id=record.game_id,
                replay_slot__isnull=True,
                status='Ok',
                **actor,
            ).order_by('time')
            before = list(attempts.filter(time__lte=record.completed_at))
            all_success = list(attempts)
            if before:
                completed_current.append(task.id)
                attempt_success_timestamps[str(task.id)] = [_timestamp(a.time) for a in all_success]
            else:
                missing_current.append(task.id)
                historical_missing_evidence[str(task.id)] = {
                    'successful_attempts_before_completion': [],
                    'completed_chain_states_before_completion': [],
                }
                if all_success:
                    attempt_success_timestamps[str(task.id)] = [_timestamp(a.time) for a in all_success]

        currently_complete = is_task_group_complete(
            task_group=record.task_group,
            game=record.game,
            team=record.team,
            user=record.user,
            anon_key=record.anon_key,
            mode='general',
            replay_slot=None,
        )
        if currently_complete:
            classification = 'VALID'
            reason = 'Current persisted state completes every visible required task.'
            confidence = 'high'
            proposed_action = 'KEEP'
        else:
            chain_times = [
                stamp
                for values in chain_completion_timestamps.values()
                for stamp in values
            ]
            near_chain = any(
                stamp and abs(
                    (record.completed_at - datetime.fromisoformat(stamp)).total_seconds()
                ) <= 300
                for stamp in chain_times
            )
            if near_chain and missing_current:
                classification = 'CONFIRMED_INVALID'
                reason = (
                    'A completed chain state is temporally adjacent to the recorded completion, '
                    'while required tasks are currently incomplete; this matches the old chain '
                    'backfill mechanism. Historical task/group versions are unavailable.'
                )
                confidence = 'medium'
                proposed_action = 'DELETE_CANDIDATE'
            else:
                classification = 'AMBIGUOUS'
                reason = (
                    'Current group state is incomplete, but the schema has no task/group version '
                    'or historical state snapshots sufficient to prove the state at completion.'
                )
                confidence = 'low'
                proposed_action = 'KEEP_REVIEW_REQUIRED'

        actor_type, actor_id = _safe_actor(record)
        return {
            'player_completed_game_id': record.id,
            'actor_type': actor_type,
            'actor_safe_id': actor_id,
            'game': record.game_id,
            'game_category': _game_category(record.game_id),
            'game_instance_id': record.game_instance_id,
            'task_group_id': record.task_group_id,
            'completion_created_at': _timestamp(record.completed_at),
            'required_tasks_current': len(tasks),
            'completed_tasks_current': len(completed_current),
            'missing_tasks_current': missing_current,
            'chain_tasks': chain_tasks,
            'chain_completion_timestamps': chain_completion_timestamps,
            'attempt_success_timestamps': attempt_success_timestamps,
            'historical_missing_evidence': historical_missing_evidence,
            'classification': classification,
            'reason': reason,
            'confidence': confidence,
            'proposed_action': proposed_action,
        }
