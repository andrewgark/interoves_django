"""
Backfill ChainTaskState for existing wall / replacements_lines attempts.

Run once after deploying the ChainTaskState feature to populate state rows for
all historical data.  Safe to run multiple times (idempotent).

Usage:
    python manage.py backfill_chain_task_states
    python manage.py backfill_chain_task_states --dry-run
    python manage.py backfill_chain_task_states --task-id 42
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from games.models import Attempt, GameTaskGroup
from games.ops.workers.jobs.recheck import recheck_chain_task


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
        parser.add_argument(
            '--require-schema-work-lock', action='store_true',
            help='Require the deployment executor MySQL named lock before each actor/task replay.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        task_id_filter = options['task_id']
        require_schema_work_lock = options['require_schema_work_lock']
        if require_schema_work_lock:
            connection.ensure_connection()
        native_connection = connection.connection
        if require_schema_work_lock:
            from games.management.commands._background_index_executor import (
                assert_schema_work_lock,
            )
            assert_schema_work_lock(native_connection)

        # This is an explicit one-time repair operation. A normal submission
        # may create only the current mode's row, so row counts or mode-pair
        # presence cannot establish whether historical attempts were replayed.
        # When invoked, rebuild every resolvable historical actor/task context.
        qs = Attempt.manager.select_related('task', 'team', 'user').filter(
            task__task_type__in=('wall', 'replacements_lines'),
        )
        if task_id_filter:
            qs = qs.filter(task_id=task_id_filter)

        seen = set()
        combos = []
        unresolved = []
        for attempt in qs.iterator():
            g = attempt.game
            if g is None:
                g = GameTaskGroup.resolve_game_for_task(attempt.task)
            if g is None:
                unresolved.append((attempt.pk, attempt.task_id))
                continue
            # Normalize explicit and inferred game IDs to one replay context.
            key = (
                attempt.team_id, attempt.user_id, attempt.anon_key,
                attempt.task_id, g.pk, attempt.replay_slot_id,
            )
            if key in seen:
                continue
            seen.add(key)
            combos.append({
                'task': attempt.task,
                'team': attempt.team,
                'user': attempt.user if attempt.user_id else None,
                'anon_key': attempt.anon_key,
                'game': g,
                'replay_slot': attempt.replay_slot,
            })

        if unresolved:
            examples = ', '.join(
                'attempt={} task={}'.format(attempt_id, task_id)
                for attempt_id, task_id in unresolved[:10]
            )
            raise CommandError(
                '{} chain attempt(s) have no resolvable game; refusing to report '
                'the backfill complete. Examples: {}'.format(len(unresolved), examples)
            )

        self.stdout.write('Found {} actor+task combination(s) to backfill.'.format(len(combos)))

        failures = []
        for i, combo in enumerate(combos, 1):
            if require_schema_work_lock:
                assert_schema_work_lock(native_connection)
            label = 'task={} game={} team={} user={} anon={}'.format(
                combo['task'].pk,
                combo['game'].pk,
                combo['team'].pk if combo['team'] else None,
                combo['user'].pk if combo['user'] else None,
                combo['anon_key'],
            )
            if dry_run:
                self.stdout.write('  [dry-run] would backfill {}'.format(label))
                continue

            try:
                recheck_chain_task(**combo, notify=False)
                self.stdout.write('  [{}/{}] OK {}'.format(i, len(combos), label))
            except Exception as e:
                self.stderr.write('  [{}/{}] ERROR {} — {}'.format(i, len(combos), label, e))
                failures.append(label)

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run — no changes made.'))
        else:
            if failures:
                raise CommandError(
                    'Backfill failed for {} of {} actor+task combination(s).'.format(
                        len(failures), len(combos),
                    )
                )
            self.stdout.write(self.style.SUCCESS('Backfill complete.'))
