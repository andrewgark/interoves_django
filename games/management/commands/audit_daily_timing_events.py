"""Report append-only timing event coverage without changing production data."""

from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Count, Max, Min

from games.models import DailySolveTiming, DailyTimingEvent, Game


def row_actor_key(row):
    if row['team_id'] is not None:
        key = 'team:{}'.format(row['team_id'])
    elif row['user_id'] is not None:
        key = 'user:{}'.format(row['user_id'])
    elif row['anon_key']:
        key = 'anon:{}'.format(row['anon_key'])
    else:
        key = ''
    if row['replay_slot_id'] is not None:
        key += ':replay:{}'.format(row['replay_slot_id'])
    return key[:160]


class Command(BaseCommand):
    help = 'Audit append-only daily timing events against timing read-model rows.'

    def add_arguments(self, parser):
        parser.add_argument('--game', help='Limit to one game id.')
        parser.add_argument('--task-group', type=int, help='Limit to one task group id.')

    def handle(self, *args, **options):
        event_filter = {}
        row_filter = {}
        if options['game']:
            event_filter['game_id'] = options['game']
            row_filter['game_id'] = options['game']
        if options['task_group']:
            event_filter['task_group_id'] = options['task_group']
            row_filter['task_group_id'] = options['task_group']

        grouped = defaultdict(Counter)
        missing_client_time = 0
        seq_regressions = 0
        unknown_actor_events = 0
        previous_seq = {}
        actor_actions = defaultdict(set)
        event_rows = DailyTimingEvent.objects.filter(**event_filter).values(
            'game_id', 'task_group_id', 'action',
        ).annotate(count=Count('id'), min_seq=Min('seq'), max_seq=Max('seq'))
        for row in event_rows.iterator(chunk_size=500):
            key = (row['game_id'], row['task_group_id'])
            grouped[key]['events'] += row['count']
            grouped[key]['actions'] += 1
            grouped[key]['{}_events'.format(row['action'])] = row['count']
            grouped[key]['min_seq'] = min(
                grouped[key].get('min_seq', row['min_seq']), row['min_seq'],
            )
            grouped[key]['max_seq'] = max(
                grouped[key].get('max_seq', row['max_seq']), row['max_seq'],
            )

        diagnostic_events = DailyTimingEvent.objects.filter(**event_filter).values(
            'game_id', 'task_group_id', 'actor_key', 'session_id', 'seq', 'action',
            'client_occurred_at', 'occurred_at',
        ).order_by('game_id', 'task_group_id', 'actor_key', 'session_id', 'occurred_at', 'pk')
        for event in diagnostic_events.iterator(chunk_size=1000):
            if event['client_occurred_at'] is None:
                missing_client_time += 1
            if event['actor_key'] in ('', 'unknown'):
                unknown_actor_events += 1
            actor_actions[(event['game_id'], event['task_group_id'], event['actor_key'])].add(
                event.get('action')
            )
            key = (
                event['game_id'], event['task_group_id'],
                event['actor_key'], event['session_id'],
            )
            previous = previous_seq.get(key)
            if previous is not None and event['seq'] < previous:
                seq_regressions += 1
            previous_seq[key] = event['seq']

        timing_counts = Counter(
            (row['game_id'], row['task_group_id'])
            for row in DailySolveTiming.objects.filter(**row_filter).values(
                'game_id', 'task_group_id',
            ).iterator(chunk_size=500)
        )
        keys = sorted(set(grouped) | set(timing_counts))
        game_names = dict(Game.objects.filter(pk__in={key[0] for key in keys}).values_list('pk', 'name'))
        timing_rows = list(DailySolveTiming.objects.filter(**row_filter).values(
            'game_id', 'task_group_id', 'team_id', 'user_id', 'anon_key', 'replay_slot_id',
            'status', 'frozen_ms',
        ).iterator(chunk_size=1000))
        row_actor_keys = {
            (row['game_id'], row['task_group_id'], row_actor_key(row))
            for row in timing_rows
        }
        zero_with_events = 0
        completed_without_complete_event = 0
        for row in timing_rows:
            actor_key = row_actor_key(row)
            actions = actor_actions.get((row['game_id'], row['task_group_id'], actor_key), set())
            if row['status'] == DailySolveTiming.STATUS_COMPLETED:
                if row['frozen_ms'] is None:
                    completed_without_complete_event += 1
                elif int(row['frozen_ms'] or 0) == 0 and actions:
                    zero_with_events += 1
                if row['frozen_ms'] is not None and actions and 'complete' not in actions:
                    completed_without_complete_event += 1
        event_actor_keys = set(
            DailyTimingEvent.objects.filter(**event_filter).values_list(
                'game_id', 'task_group_id', 'actor_key',
            ).distinct().iterator(chunk_size=1000)
        )
        self.stdout.write(
            'summary events={} client_time_missing={} seq_regressions={} '
            'unknown_actor_events={} unmatched_actor_groups={} zero_with_events={} '
            'completed_without_complete_event={}'.format(
                sum(counts.get('events', 0) for counts in grouped.values()),
                missing_client_time,
                seq_regressions,
                unknown_actor_events,
                len(event_actor_keys - row_actor_keys),
                zero_with_events,
                completed_without_complete_event,
            )
        )
        for game_id, task_group_id in keys:
            counts = grouped[(game_id, task_group_id)]
            self.stdout.write(
                'game={} name={!r} task_group={} read_rows={} events={} '
                'action_kinds={} seq={}..{}'.format(
                    game_id,
                    game_names.get(game_id, ''),
                    task_group_id,
                    timing_counts[(game_id, task_group_id)],
                    counts.get('events', 0),
                    counts.get('actions', 0),
                    counts.get('min_seq', '—'),
                    counts.get('max_seq', '—'),
                )
            )
            for action in ('start', 'resume', 'heartbeat', 'pause', 'auto_pause', 'complete'):
                if counts.get('{}_events'.format(action), 0):
                    self.stdout.write('  {}={}'.format(action, counts['{}_events'.format(action)]))
