"""Check and repair the rebuildable daily-results read model."""

import time

from django.core.management.base import BaseCommand

from games.daily_result_projection import (
    _canonical_group_results,
    projection_state_is_valid,
    refresh_daily_result_projection,
)
from games.models import DailyResultProjectionState, GameTaskGroup
from games.daily_result_projection_cron import daily_result_projection_cron_lock


class Command(BaseCommand):
    help = 'Reconcile daily result projections (dry-run unless --apply is supplied).'

    def add_arguments(self, parser):
        parser.add_argument('--game')
        parser.add_argument('--release', action='append', dest='releases')
        parser.add_argument('--task-group', type=int)
        parser.add_argument('--limit', type=int, default=10)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--deep', action='store_true',
            help='Recalculate and compare canonical actor scores, including valid releases.',
        )

    def handle(self, *args, **options):
        started = time.perf_counter()
        qs = GameTaskGroup.objects.filter(
            game__project_id='sections',
        ).select_related('game', 'task_group').order_by('game_id', 'pk')
        if options['game']:
            qs = qs.filter(game_id=options['game'])
        if options['task_group']:
            qs = qs.filter(task_group_id=options['task_group'])
        if options['releases']:
            qs = qs.filter(number__in=[str(value) for value in options['releases']])

        limit = max(1, int(options['limit']))
        scanned = valid = missing = stale = rebuilt = failed = 0
        repairs_considered = 0
        with daily_result_projection_cron_lock() as acquired:
            if not acquired:
                self.stdout.write('projection reconciliation skipped: lock held')
                return
            for link in qs.iterator(chunk_size=min(limit, 100)):
                if not options['apply'] and scanned >= limit:
                    break
                scanned += 1
                state = DailyResultProjectionState.objects.filter(
                    game=link.game, task_group=link.task_group,
                ).first()
                state_valid = projection_state_is_valid(state, link.game)
                needs_repair = not state_valid
                if state is None:
                    missing += 1
                    reason = 'missing_state'
                elif not state_valid:
                    stale += 1
                    reason = 'invalid_state'
                else:
                    valid += 1
                    reason = 'valid'

                canonical_count = None
                if options['deep'] or (needs_repair and not options['apply']):
                    canonical_count = len(_canonical_group_results(link.game, link.task_group))

                if options['apply'] and (needs_repair or options['deep']):
                    if repairs_considered >= limit:
                        break
                    repairs_considered += 1
                    refresh_daily_result_projection(link.game, link.task_group)
                    state = DailyResultProjectionState.objects.filter(
                        game=link.game, task_group=link.task_group,
                    ).first()
                    if projection_state_is_valid(state, link.game):
                        rebuilt += 1
                    else:
                        failed += 1

                details = ' canonical_actors={}'.format(canonical_count) if canonical_count is not None else ''
                self.stdout.write(
                    '{} release={} task_group={} status={}{}{}'.format(
                        link.game_id, link.number, link.task_group_id, reason,
                        ' repaired' if options['apply'] and needs_repair and state_valid is False else '',
                        details,
                    )
                )

        mode = 'apply' if options['apply'] else 'dry-run'
        self.stdout.write(self.style.SUCCESS(
            'mode={} scanned={} valid={} missing={} stale={} rebuilt={} failed={} elapsed_s={:.3f}'.format(
                mode, scanned, valid, missing, stale, rebuilt, failed,
                time.perf_counter() - started,
            )
        ))
