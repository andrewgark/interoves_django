from django.core.management.base import BaseCommand

from games.difficulty import SUPPORTED_GAME_IDS, backfill_daily_difficulty_rows


class Command(BaseCommand):
    help = 'Create missing DailyGameDifficulty rows for existing daily-game editions.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--game',
            action='append',
            choices=SUPPORTED_GAME_IDS,
            dest='games',
            help='Limit to one game id; may be repeated.',
        )
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        created = backfill_daily_difficulty_rows(
            game_ids=options.get('games'),
            dry_run=options['dry_run'],
        )
        prefix = 'Would create' if options['dry_run'] else 'Created'
        self.stdout.write(self.style.SUCCESS(
            '{} {} DailyGameDifficulty row(s).'.format(prefix, created),
        ))
