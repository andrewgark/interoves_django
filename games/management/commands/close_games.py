"""Close games by setting end_time in the past and clearing is_testing."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from games.models import Game


class Command(BaseCommand):
    help = "Close games: end_time=yesterday, is_testing=False."

    def add_arguments(self, parser):
        parser.add_argument('game_ids', nargs='+', help='Game primary keys to close.')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would change without saving.',
        )

    def handle(self, *args, **options):
        closed_at = timezone.now() - timedelta(days=1)
        for game_id in options['game_ids']:
            game = Game.objects.filter(pk=game_id).first()
            if game is None:
                self.stderr.write(self.style.WARNING('Not found: {}'.format(game_id)))
                continue
            if options['dry_run']:
                self.stdout.write('Would close {} (end_time → {})'.format(game_id, closed_at))
                continue
            Game.objects.filter(pk=game_id).update(end_time=closed_at, is_testing=False)
            self.stdout.write(self.style.SUCCESS('Closed {}'.format(game_id)))
