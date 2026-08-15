from datetime import timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from games.models import PlayerStartedGame


class Command(BaseCommand):
    help = 'Report backend game starts and acknowledged Metrika delivery by local day.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=14)
        parser.add_argument('--game', dest='game_kind')
        parser.add_argument('--timezone', default='Europe/Moscow')

    def handle(self, *args, **options):
        days = max(1, int(options['days']))
        tz = ZoneInfo(options['timezone'])
        since = timezone.now() - timedelta(days=days)
        qs = PlayerStartedGame.objects.filter(started_at__gte=since)
        if options.get('game_kind'):
            qs = qs.filter(game_kind=options['game_kind'])
        rows = (
            qs.annotate(day=TruncDate('started_at', tzinfo=tz))
            .values('day', 'game_kind')
            .annotate(
                starts=Count('id'),
                live=Count('id', filter=Q(is_backfilled=False)),
                backfilled=Count('id', filter=Q(is_backfilled=True)),
                metrika_acked=Count(
                    'id',
                    filter=Q(is_backfilled=False, metrika_acked_at__isnull=False),
                ),
            )
            .order_by('day', 'game_kind')
        )
        self.stdout.write('date\tgame\tstarts\tlive\tbackfilled\tmetrika_acked\tcoverage')
        for row in rows:
            live = row['live'] or 0
            coverage = '{:.1f}%'.format(100 * row['metrika_acked'] / live) if live else '—'
            self.stdout.write('{}\t{}\t{}\t{}\t{}\t{}\t{}'.format(
                row['day'],
                row['game_kind'],
                row['starts'],
                live,
                row['backfilled'],
                row['metrika_acked'],
                coverage,
            ))
