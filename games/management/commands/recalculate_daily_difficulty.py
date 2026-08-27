import json

from django.core.management.base import BaseCommand, CommandError

from games.difficulty import (
    SUPPORTED_GAME_IDS,
    recalculate_all_daily_difficulties,
)
from games.models import DailyGameDifficulty, GameTaskGroup


def _fmt(value, digits=2):
    if value is None:
        return '—'
    return ('{:.' + str(digits) + 'f}').format(float(value))


class Command(BaseCommand):
    help = 'Manually rebuild cached difficulty for Ladder, Alphabetty and Salad editions.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--game',
            action='append',
            choices=SUPPORTED_GAME_IDS,
            dest='games',
            help='Limit to one game id; may be repeated.',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            dest='rebuild_all',
            help='Rebuild every supported edition (default when no placement is given).',
        )
        parser.add_argument(
            '--placement-id',
            type=int,
            dest='placement_id',
            help='Rebuild a single GameTaskGroup placement.',
        )
        parser.add_argument('--json', action='store_true', dest='as_json')
        parser.add_argument(
            '--min-n',
            type=int,
            default=5,
            help='Only print rows with at least this many unique players.',
        )

    def handle(self, *args, **options):
        min_n = options['min_n']
        if min_n < 0:
            raise CommandError('--min-n must be non-negative')
        placement = None
        placement_id = options.get('placement_id')
        if placement_id:
            placement = GameTaskGroup.objects.filter(pk=placement_id).select_related(
                'game', 'task_group',
            ).first()
            if placement is None:
                raise CommandError('Unknown placement id {}'.format(placement_id))
            snapshot = DailyGameDifficulty.objects.filter(placement=placement).first()
            if snapshot is not None:
                DailyGameDifficulty.objects.filter(pk=snapshot.pk).update(
                    refresh_claim_token=None,
                    refresh_claimed_until=None,
                )
        results = recalculate_all_daily_difficulties(
            game_ids=options.get('games'),
            placement=placement,
        )
        shown = [row for row in results if row and row['n'] >= min_n]
        if options['as_json']:
            self.stdout.write(json.dumps(shown, ensure_ascii=False, sort_keys=True))
            return

        headers = (
            'game', 'number', 'N', 'median_time', 'median_errors', 'help_rate',
            'unfinished_rate', 'raw', 'adjusted', 'stars',
        )
        self.stdout.write('\t'.join(headers))
        for row in shown:
            metrics = row['metrics']
            stars = '{}{}'.format('★' * row['stars'], '☆' * (5 - row['stars']))
            self.stdout.write('\t'.join((
                row['game_id'],
                str(row['number']),
                str(row['n']),
                _fmt(metrics.get('median_time'), 1),
                _fmt(metrics.get('median_errors'), 1),
                _fmt(metrics.get('help_rate'), 3),
                _fmt(metrics.get('unfinished_rate'), 3),
                _fmt(row.get('raw_rating'), 2),
                _fmt(row.get('adjusted_rating'), 2),
                stars,
            )))
        self.stdout.write(self.style.SUCCESS(
            'Recalculated {} editions; printed {} with N >= {}.'.format(
                len(results), len(shown), min_n,
            )
        ))
