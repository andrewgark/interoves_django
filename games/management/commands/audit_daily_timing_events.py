"""Report append-only timing event coverage without changing production data."""

from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Count, Max, Min

from games.models import DailySolveTiming, DailyTimingEvent, Game


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

        timing_counts = Counter(
            (row['game_id'], row['task_group_id'])
            for row in DailySolveTiming.objects.filter(**row_filter).values(
                'game_id', 'task_group_id',
            ).iterator(chunk_size=500)
        )
        keys = sorted(set(grouped) | set(timing_counts))
        game_names = dict(Game.objects.filter(pk__in={key[0] for key in keys}).values_list('pk', 'name'))
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
