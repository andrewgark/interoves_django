"""Dry-run by default; explicitly rebuild canonical section score projections."""
from django.core.management.base import BaseCommand

from decimal import Decimal

from games.daily_result_projection import (
    _canonical_group_results,
    refresh_daily_result_projection,
    scorer_adapter_version,
)
from games.models import DailyResultProjection, DailyResultProjectionState, GameTaskGroup


class Command(BaseCommand):
    help = 'Rebuild derived canonical score rows for daily section releases (default: dry-run).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Persist projection rows; omitted means dry-run.')
        parser.add_argument('--game', help='Limit to one game id.')
        parser.add_argument('--task-group', type=int, help='Limit to one TaskGroup id.')
        parser.add_argument('--batch-size', type=int, default=100, help='Maximum releases handled in one batch.')
        parser.add_argument(
            '--reconcile', action='store_true',
            help='Compare persisted projections to canonical sources; always read-only.',
        )

    def handle(self, *args, **options):
        qs = GameTaskGroup.objects.filter(game__project_id='sections').select_related('game', 'task_group').order_by('game_id', 'pk')
        if options['game']:
            qs = qs.filter(game_id=options['game'])
        if options['task_group']:
            qs = qs.filter(task_group_id=options['task_group'])
        batch_size = max(1, min(int(options['batch_size']), 1000))
        scanned = written = 0
        mode = 'apply' if options['apply'] else 'dry-run'
        self.stdout.write('mode={} batch_size={}'.format(mode, batch_size))
        for link in qs.iterator(chunk_size=batch_size):
            scanned += 1
            results = _canonical_group_results(link.game, link.task_group)
            source = {
                (key[0], str(key[1])): Decimal(str(data['score'] or 0))
                for key, data in results.items() if data['present']
            }
            count = len(source)
            if options['reconcile']:
                projected = {
                    (row.actor_type, row.actor_key): row.score
                    for row in DailyResultProjection.objects.filter(
                        game=link.game, task_group=link.task_group,
                    )
                }
                missing = sorted(set(source) - set(projected))
                extra = sorted(set(projected) - set(source))
                different = sorted(key for key in set(source) & set(projected) if source[key] != projected[key])
                state = DailyResultProjectionState.objects.filter(
                    game=link.game, task_group=link.task_group,
                ).first()
                expected_version = scorer_adapter_version(link.game, link.task_group)
                stale = state is None or state.adapter_version != expected_version
                self.stdout.write(
                    '{} task_group={} canonical={} missing={} extra={} score_mismatch={} stale_version={}'.format(
                        link.game_id, link.task_group_id, count, len(missing), len(extra),
                        len(different), stale,
                    )
                )
                if missing:
                    self.stdout.write('  missing actors: {}'.format(', '.join('{}:{}'.format(*key) for key in missing[:20])))
                if extra:
                    self.stdout.write('  extra actors: {}'.format(', '.join('{}:{}'.format(*key) for key in extra[:20])))
                if different:
                    self.stdout.write('  score mismatches: {}'.format(', '.join('{}:{}'.format(*key) for key in different[:20])))
            else:
                self.stdout.write('{} task_group={} canonical_actors={}'.format(link.game_id, link.task_group_id, count))
            if options['apply'] and not options['reconcile']:
                written += refresh_daily_result_projection(link.game, link.task_group, results=results)
        if options['reconcile']:
            self.stdout.write(self.style.SUCCESS('{} releases reconciled (read-only).'.format(scanned)))
        else:
            self.stdout.write(self.style.SUCCESS('{} releases scanned; {} projection rows written.'.format(scanned, written)))
