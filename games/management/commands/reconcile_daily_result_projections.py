"""Check and repair the rebuildable daily-results read model."""

from django.core.management.base import BaseCommand

from games.daily_result_projection_cron import reconcile_projection_releases


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
        result = reconcile_projection_releases(
            apply=options['apply'],
            limit=options['limit'],
            game=options['game'],
            releases=options['releases'],
            task_group=options['task_group'],
            deep=options['deep'],
        )
        for line in result['lines']:
            self.stdout.write(line)
        if result['skipped_locked']:
            return
        self.stdout.write(self.style.SUCCESS(result['summary']))
