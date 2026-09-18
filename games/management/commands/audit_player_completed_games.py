import hashlib
import json
import time
from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Prefetch, Q

from games.analytics import is_task_completion_state
from games.models import Attempt, ChainTaskState, PlayerCompletedGame, Task

CHAIN_TASK_TYPES = {'raddle', 'replacements_lines', 'alphabetty', 'word_salad'}
BUG_START = datetime(2026, 8, 15, 13, 48, 5, tzinfo=timezone.utc)
BUG_END = datetime(2026, 9, 17, 22, 50, 20, tzinfo=timezone.utc)


def actor_key(row):
    if row.team_id:
        return ('team', row.team_id)
    if row.user_id:
        return ('user', row.user_id)
    return ('anon', row.anon_key)


def safe_actor(row):
    kind, value = actor_key(row)
    return kind, hashlib.sha256(value.encode()).hexdigest()[:16] if kind == 'anon' else str(value)


def category(game_id):
    return {'replacements': 'replacements', 'ladder': 'raddle',
            'alphabetty': 'alphabetty', 'salad': 'word_salad'}.get(game_id, 'other')


def iso(value):
    return value.isoformat() if value else None


class Command(BaseCommand):
    help = 'Read-only forensic audit of PlayerCompletedGame rows.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', required=True)
        parser.add_argument('--suspect-only', action='store_true')
        parser.add_argument('--json', action='store_true', dest='json_output')
        parser.add_argument('--id', dest='record_ids', action='append', type=int)
        parser.add_argument('--game', dest='game_id')
        parser.add_argument('--batch-size', type=int, default=100)

    def handle(self, *args, **options):
        if not options['dry_run']:
            raise CommandError('Read-only audit requires --dry-run.')
        qs = PlayerCompletedGame.objects.order_by('pk')
        if options.get('record_ids'):
            qs = qs.filter(pk__in=options['record_ids'])
        if options.get('game_id'):
            qs = qs.filter(game_id=options['game_id'])
        if options['suspect_only']:
            # The task-group forensic universe is structural: tournament
            # games use task-group completion, while section daily games are
            # explicitly non-tournament at the Game model boundary.  Current
            # incompleteness is decided from the persisted batch snapshot
            # below; dates and backfill flags are forensic evidence only.
            qs = qs.filter(game__is_tournament=True)
        items = []
        query_count = 0
        started = time.monotonic()
        record_ids = list(qs.values_list('pk', flat=True))
        batch_size = max(1, options['batch_size'])
        for offset in range(0, len(record_ids), batch_size):
            records = list(PlayerCompletedGame.objects.select_related('game', 'task_group').prefetch_related(
                Prefetch('task_group__tasks', queryset=Task.objects.filter(is_removed=False).order_by('pk'))
            ).filter(pk__in=record_ids[offset:offset + batch_size]).order_by('pk'))
            snapshot = self._snapshot(records)
            query_count += snapshot['query_count']
            for record in records:
                item = self._audit(record, snapshot)
                if options['suspect_only'] and not item['missing_tasks_current']:
                    continue
                items.append(item)
        summary = self._summary(items, query_count)
        summary['candidate_count'] = len(record_ids)
        if options['json_output']:
            for item in items:
                self.stdout.write(json.dumps(item, ensure_ascii=False, sort_keys=True))
        else:
            for key in ('total_suspect', 'valid', 'confirmed_invalid', 'ambiguous'):
                self.stdout.write('{}: {}'.format(key, summary[key]))
            self.stdout.write('CONFIRMED_INVALID_IDS={}'.format(summary['confirmed_invalid_ids']))
            self.stdout.write('AMBIGUOUS_IDS={}'.format(summary['ambiguous_ids']))
            self.stdout.write('breakdown={}'.format(json.dumps(summary['breakdown'], ensure_ascii=False, sort_keys=True)))
            for item in items:
                if item['classification'] == 'CONFIRMED_INVALID':
                    self.stdout.write('evidence={}'.format(json.dumps({
                        'pcg_id': item['player_completed_game_id'], 'game': item['game'],
                        'task_group_id': item['task_group_id'],
                        'completion_created_at': item['completion_created_at'],
                        'trigger_chain_task_id': item['trigger_chain_task_id'],
                        'trigger_chain_completed_at': item['trigger_chain_completed_at'],
                        'missing_at_completion': item['missing_at_completion'],
                        'classification_reason': item['reason'],
                    }, ensure_ascii=False, sort_keys=True)))
        self.stdout.write('query_count={}'.format(query_count))
        self.stdout.write('candidate_count={}'.format(summary['candidate_count']))
        self.stdout.write('runtime_seconds={:.3f}'.format(time.monotonic() - started))

    def _snapshot(self, records):
        tasks_by_group = {}
        task_ids = set()
        actor_q = Q()
        game_ids = set()
        for record in records:
            game_ids.add(record.game_id)
            actor_q |= Q(team_id=record.team_id) if record.team_id else Q(user_id=record.user_id) if record.user_id else Q(anon_key=record.anon_key)
            tasks_by_group[record.task_group_id] = list(record.task_group.tasks.all())
            task_ids.update(task.id for task in tasks_by_group[record.task_group_id])
        attempts = Attempt.manager.filter(
            actor_q,
            game_id__in=game_ids,
            task_id__in=task_ids,
            replay_slot__isnull=True,
        ).exclude(skip=True).order_by('time') if task_ids else []
        states = ChainTaskState.objects.filter(actor_q, game_id__in=game_ids, task_id__in=task_ids,
                                                replay_slot__isnull=True).order_by('updated_at') if task_ids else []
        attempt_map, state_map = {}, {}
        for row in attempts:
            attempt_map.setdefault((actor_key(row), row.game_id, row.task_id), []).append(row)
        for row in states:
            state_map.setdefault((actor_key(row), row.game_id, row.task_id), []).append(row)
        return {'tasks': tasks_by_group, 'attempts': attempt_map, 'states': state_map,
                'query_count': 4 if records else 0}

    def _audit(self, record, snapshot):
        actor = actor_key(record)
        tasks = snapshot['tasks'].get(record.task_group_id, [])
        completed, missing, chains, chain_times, attempt_times = [], [], [], {}, {}
        missing_at, trigger_task, trigger_time = [], None, None
        for task in tasks:
            key = (actor, record.game_id, task.id)
            if task.task_type in CHAIN_TASK_TYPES:
                chains.append({'id': task.id, 'type': task.task_type})
                rows = snapshot['states'].get(key, [])
                current = next((row for row in reversed(rows) if row.game_mode == 'general'), None)
                solved = [row for row in rows if is_task_completion_state(task, row.state)]
                if current and is_task_completion_state(task, current.state):
                    completed.append(task.id)
                    chain_times[str(task.id)] = [iso(row.updated_at) for row in solved]
                    before = [row for row in solved if row.updated_at <= record.completed_at]
                    if before and (trigger_time is None or before[-1].updated_at > trigger_time):
                        trigger_task, trigger_time = task.id, before[-1].updated_at
                else:
                    missing.append(task.id)
                    if not any(row.updated_at <= record.completed_at for row in solved):
                        missing_at.append(task.id)
            else:
                rows = [row for row in snapshot['attempts'].get(key, []) if row.status == 'Ok']
                before = [row for row in rows if row.time <= record.completed_at]
                if before:
                    completed.append(task.id)
                    attempt_times[str(task.id)] = [iso(row.time) for row in rows]
                else:
                    missing.append(task.id); missing_at.append(task.id)
                    if rows:
                        attempt_times[str(task.id)] = [iso(row.time) for row in rows]
        current_complete = bool(tasks) and not missing
        in_window = BUG_START <= record.completed_at.astimezone(timezone.utc) <= BUG_END
        near_trigger = trigger_time and abs((record.completed_at - trigger_time).total_seconds()) <= 300
        if current_complete:
            classification, reason, confidence, action = 'VALID', 'All current required tasks have completion evidence.', 'high', 'KEEP'
        elif in_window and near_trigger and missing_at:
            classification, reason, confidence, action = 'CONFIRMED_INVALID', 'Completed chain state immediately preceded completion while required task evidence was missing; matches legacy backfill bug.', 'medium', 'DELETE_CANDIDATE'
        else:
            classification, reason, confidence, action = 'AMBIGUOUS', 'Current group is incomplete, but historical membership/state snapshots are insufficient to prove premature completion.', 'low', 'KEEP_REVIEW_REQUIRED'
        actor_type, actor_id = safe_actor(record)
        return {'player_completed_game_id': record.id, 'actor_type': actor_type, 'actor_safe_id': actor_id,
                'game': record.game_id, 'game_category': category(record.game_id),
                'game_instance_id': record.game_instance_id, 'task_group_id': record.task_group_id,
                'completion_created_at': iso(record.completed_at), 'required_tasks_current': len(tasks),
                'completed_tasks_current': len(completed), 'missing_tasks_current': missing,
                'chain_tasks': chains, 'chain_completion_timestamps': chain_times,
                'attempt_success_timestamps': attempt_times, 'missing_at_completion': missing_at,
                'trigger_chain_task_id': trigger_task, 'trigger_chain_completed_at': iso(trigger_time),
                'classification': classification, 'reason': reason, 'confidence': confidence,
                'proposed_action': action, 'candidate_reason': 'current_task_group_incomplete',
                'outside_known_bug_window': not in_window}

    def _summary(self, items, query_count):
        breakdown, counts = {}, {'VALID': 0, 'CONFIRMED_INVALID': 0, 'AMBIGUOUS': 0}
        for item in items:
            cls = item['classification']; counts[cls] += 1
            row = breakdown.setdefault(item['game_category'], {'suspect': 0, 'valid': 0, 'confirmed_invalid': 0, 'ambiguous': 0})
            row['suspect'] += 1; row[cls.lower()] += 1
        return {'total_suspect': len(items), **{key.lower(): value for key, value in counts.items()},
                'confirmed_invalid_ids': [i['player_completed_game_id'] for i in items if i['classification'] == 'CONFIRMED_INVALID'],
                'ambiguous_ids': [i['player_completed_game_id'] for i in items if i['classification'] == 'AMBIGUOUS'],
                'breakdown': breakdown, 'query_count': query_count}
