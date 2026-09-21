"""Read-only coverage audit for canonical daily section results and clocks."""
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from games.daily_result_projection import _canonical_group_results
from games.daily_section import is_daily_timing_game
from games.models import DailySolveTiming, Game, GameTaskGroup


class Command(BaseCommand):
    help = 'Report canonical result timing coverage by section game and actor type (read-only).'

    def add_arguments(self, parser):
        parser.add_argument('--game', help='Limit to one game id.')

    def handle(self, *args, **options):
        games = Game.objects.filter(project_id='sections').order_by('pk')
        if options['game']:
            games = games.filter(pk=options['game'])
        totals = defaultdict(Counter)
        for game in games.iterator(chunk_size=50):
            if not is_daily_timing_game(game.pk):
                continue
            links = GameTaskGroup.objects.filter(game=game).select_related('task_group').order_by('pk')
            for link in links.iterator(chunk_size=100):
                results = _canonical_group_results(game, link.task_group)
                source = {}
                for key, row in results.items():
                    if row['present']:
                        source[key] = row['actor']
                timing_rows = list(DailySolveTiming.objects.filter(
                    game=game, task_group=link.task_group, replay_slot__isnull=True,
                ).values('team_id', 'user_id', 'anon_key', 'timing_version', 'status', 'accumulated_ms', 'frozen_ms'))
                timings = {}
                duplicates = Counter()
                invalid = 0
                for row in timing_rows:
                    key = ('team', row['team_id']) if row['team_id'] is not None else (
                        ('user', row['user_id']) if row['user_id'] is not None else ('anon', row['anon_key'])
                    )
                    duplicates[key] += 1
                    timings[key] = row
                    duration = row['frozen_ms'] if row['status'] == DailySolveTiming.STATUS_COMPLETED else row['accumulated_ms']
                    if int(row['timing_version'] or 0) < DailySolveTiming.TIMING_VERSION_ACTIVE or int(duration or 0) < 0:
                        invalid += 1
                duplicate_count = sum(count - 1 for count in duplicates.values() if count > 1)
                for key in source:
                    kind = key[0]
                    totals[game.pk][kind + '_results'] += 1
                    row = timings.get(key)
                    if row is not None and int(row['timing_version'] or 0) >= DailySolveTiming.TIMING_VERSION_ACTIVE:
                        totals[game.pk][kind + '_timed'] += 1
                    else:
                        totals[game.pk][kind + '_missing'] += 1
                totals[game.pk]['invalid_or_duplicate'] += invalid + duplicate_count

        for game_id in games.values_list('pk', flat=True):
            if not is_daily_timing_game(game_id):
                continue
            counts = totals[game_id]
            self.stdout.write('game={} invalid_or_duplicate={}'.format(game_id, counts['invalid_or_duplicate']))
            for kind in ('user', 'team', 'anon'):
                result_count = counts[kind + '_results']
                timed = counts[kind + '_timed']
                missing = counts[kind + '_missing']
                pct = (100.0 * timed / result_count) if result_count else 100.0
                self.stdout.write('  actor={} results={} timed={} missing={} coverage={:.1f}%'.format(
                    kind, result_count, timed, missing, pct,
                ))
