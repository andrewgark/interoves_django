from django.core.management.base import BaseCommand

from games.difficulty import DUE_REFRESH_LIMIT, SUPPORTED_GAME_IDS
from games.difficulty_refresh import refresh_due_daily_difficulties


class Command(BaseCommand):
    help = 'Claim and refresh due daily-game difficulty snapshots (for cron).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--game',
            action='append',
            choices=SUPPORTED_GAME_IDS,
            dest='games',
            help='Limit to one game id; may be repeated.',
        )
        parser.add_argument(
            '--game-type',
            action='append',
            choices=SUPPORTED_GAME_IDS,
            dest='games',
            help='Alias of --game.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=DUE_REFRESH_LIMIT,
            help='Max editions to claim in this tick (default {}).'.format(DUE_REFRESH_LIMIT),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List due rows without claiming or calculating.',
        )

    def handle(self, *args, **options):
        results = refresh_due_daily_difficulties(
            game_ids=options.get('games'),
            limit=options['limit'],
            dry_run=options['dry_run'],
        )
        if not results:
            self.stdout.write('No daily difficulties were due.')
            return
        labels = [
            '{}/{}'.format(row['game_id'], row['number'])
            for row in results
        ]
        prefix = 'Due' if options['dry_run'] else 'Refreshed'
        self.stdout.write(self.style.SUCCESS(
            '{} {}: {}.'.format(prefix, len(results), ', '.join(labels)),
        ))
