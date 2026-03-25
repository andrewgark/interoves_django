"""
Backfill ChainTaskState for all existing wall / replacements_lines attempts.

Run once after deploying the ChainTaskState feature to populate state rows for
all historical data.  Safe to run multiple times (idempotent).

Usage:
    python manage.py backfill_chain_task_states
    python manage.py backfill_chain_task_states --dry-run
    python manage.py backfill_chain_task_states --task-id 42
"""
from django.core.management.base import BaseCommand

from games.models import Attempt, CHAIN_TASK_TYPES
from games.recheck import recheck_chain_task


class Command(BaseCommand):
    help = 'Backfill ChainTaskState rows for wall and replacements_lines tasks.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would be done without making changes.',
        )
        parser.add_argument(
            '--task-id', type=int, default=None,
            help='Limit backfill to a single task ID.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        task_id_filter = options['task_id']

        # Collect unique (team_id, user_id, anon_key, task) actor+task combos
        # by scanning all attempts for chain task types.
        qs = Attempt.manager.select_related('task', 'team', 'user').filter(
            task__task_type__in=CHAIN_TASK_TYPES,
        )
        if task_id_filter:
            qs = qs.filter(task_id=task_id_filter)

        seen = set()
        combos = []
        for attempt in qs.iterator():
            key = (attempt.team_id, attempt.user_id, attempt.anon_key, attempt.task_id)
            if key in seen:
                continue
            seen.add(key)
            combos.append({
                'task': attempt.task,
                'team': attempt.team,
                'user': attempt.user if attempt.user_id else None,
                'anon_key': attempt.anon_key,
            })

        self.stdout.write('Found {} actor+task combination(s) to backfill.'.format(len(combos)))

        for i, combo in enumerate(combos, 1):
            label = 'task={} team={} user={} anon={}'.format(
                combo['task'].pk,
                combo['team'].pk if combo['team'] else None,
                combo['user'].pk if combo['user'] else None,
                combo['anon_key'],
            )
            if dry_run:
                self.stdout.write('  [dry-run] would backfill {}'.format(label))
                continue

            try:
                recheck_chain_task(**combo)
                self.stdout.write('  [{}/{}] OK {}'.format(i, len(combos), label))
            except Exception as e:
                self.stderr.write('  [{}/{}] ERROR {} — {}'.format(i, len(combos), label, e))

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run — no changes made.'))
        else:
            self.stdout.write(self.style.SUCCESS('Backfill complete.'))
