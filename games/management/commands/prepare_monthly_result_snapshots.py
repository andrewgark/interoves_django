"""Inspect or enqueue saved monthly tables; scoring never runs in HTTP."""

from django.core.management.base import BaseCommand, CommandError

from games.daily.section import is_scheduled_game
from games.models import DailyAggregateSnapshot, Game
from games.results.standings.aggregate_leaderboard import _monthly_columns
from games.results.aggregate_snapshots import request_monthly_snapshot, refresh_monthly_snapshot
from games.runtime import RUNTIME_ROLE_BACKGROUND, runtime_role


class Command(BaseCommand):
    help = 'Inspect monthly table coverage; --apply durably queues builds for the background worker.'

    def add_arguments(self, parser):
        parser.add_argument('--game', action='append', dest='games')
        parser.add_argument('--month', action='append', dest='months', help='YYYY-MM; default: latest published month')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--process', action='store_true', help='Also build on the background worker runtime only')

    def handle(self, *args, **options):
        if options['process'] and (not options['apply'] or runtime_role() != RUNTIME_ROLE_BACKGROUND):
            raise CommandError('--process requires --apply and the background-worker runtime')
        games = Game.objects.filter(project_id='sections').order_by('pk')
        if options['games']:
            games = games.filter(pk__in=options['games'])
        for game in games:
            if not is_scheduled_game(game.pk):
                continue
            for requested in options['months'] or [None]:
                columns, month, *_ = _monthly_columns(game, requested)
                if month is None:
                    self.stdout.write('{}: no published releases'.format(game.pk))
                    continue
                if requested and month.strftime('%Y-%m') != requested:
                    raise CommandError('{}: no published releases for {}'.format(game.pk, requested))
                if not options['apply']:
                    row = DailyAggregateSnapshot.objects.filter(game=game, month=month).select_related(
                        'published_version',
                    ).first()
                    self.stdout.write('{} {} releases={} published={} pending={} attempts={} retry={} error={} (read-only)'.format(
                        game.pk, month, len(columns),
                        row.published_version.published_at if row and row.published_version else None,
                        row.requested_revision > row.completed_revision if row else True,
                        row.attempts if row else 0, row.next_attempt_at if row else None,
                        row.last_error if row else '',
                    ))
                    continue
                snapshot, _ = request_monthly_snapshot(game, month, columns)
                status = refresh_monthly_snapshot(snapshot.pk) if options['process'] else 'queued'
                self.stdout.write('{} {} snapshot={} status={}'.format(game.pk, month, snapshot.pk, status))
