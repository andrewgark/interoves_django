"""Run an isolated production smoke test for PendingAttempt bulk actions.

This command intentionally exercises the same service layer as the admin
actions, but uses a marker and restores all touched admin/task state after a
successful run.  It is not a substitute for the unit tests: it verifies the
live database, durable queue, and deployed worker together.
"""

from __future__ import annotations

import json
import time
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from games.attempt_admin_bulk import accept_pending_attempts, reject_pending_attempts
from games.models import (
    Attempt,
    ChainTaskState,
    Task,
    Team,
    WordSaladRecheckJob,
    WordSaladRecheckItem,
    WordSaladRecheckOutbox,
    Game,
)


class Command(BaseCommand):
    help = 'Run an isolated live smoke test for PendingAttempt ДА/НЕТ and replay.'

    def add_arguments(self, parser):
        parser.add_argument('--task', type=int, required=True)
        parser.add_argument('--game', required=True)
        parser.add_argument('--team', default='admin')
        parser.add_argument('--wait-seconds', type=int, default=180)
        parser.add_argument('--confirm-production', action='store_true')

    def handle(self, *args, **options):
        if not options['confirm_production']:
            raise CommandError(
                'This command writes and cleans up production data; '
                'pass --confirm-production to run it.'
            )
        if options['wait_seconds'] < 1 or options['wait_seconds'] > 900:
            raise CommandError('--wait-seconds must be between 1 and 900')

        task = Task.objects.get(pk=options['task'])
        game = Game.objects.get(pk=options['game'])
        team = Team.objects.get(pk=options['team'])
        if task.task_type not in ('wall', 'replacements_lines', 'raddle', 'alphabetty', 'word_salad'):
            raise CommandError('task is not a supported chain task')

        active_jobs = WordSaladRecheckJob.objects.filter(
            task=task, game=game,
            status__in=(WordSaladRecheckJob.STATUS_PENDING, WordSaladRecheckJob.STATUS_RUNNING),
        ).exists()
        if active_jobs:
            raise CommandError('an active replay job already exists for this task/game')

        run_id = uuid.uuid4().hex[:12]
        marker = 'PROD-BULK-TEST-{}'.format(run_id)
        original_checker_data = task.checker_data
        state_snapshot = {
            row.pk: (row.state, row.last_attempt_id, row.updated_at)
            for row in ChainTaskState.objects.filter(task=task, game=game, team=team)
        }
        attempt_snapshot = {
            row.pk: {
                field: getattr(row, field)
                for field in ('status', 'possible_status', 'points', 'state', 'comment', 'skip')
            }
            for row in Attempt.manager.filter(task=task, game=game, team=team)
        }
        before_job_ids = set(
            WordSaladRecheckJob.objects.filter(task=task, game=game).values_list('pk', flat=True)
        )
        attempts = []
        job_ids = set()
        result = {'run_id': run_id, 'marker': marker, 'checks': {}}

        try:
            attempts = self._create_attempts(task, game, team, marker)
            accepted, rejected, unrelated = attempts

            rejected_count = reject_pending_attempts([rejected.pk])
            if rejected_count != 1:
                raise CommandError('reject action did not reject exactly one attempt')

            receipt = accept_pending_attempts([accepted.pk], return_receipt=True)
            job_ids = {
                row['id'] for row in receipt['queue_receipt']['jobs']
                if row.get('created') and row['id'] not in before_job_ids
            }
            if not job_ids:
                raise CommandError('accept action did not create an isolated replay job')

            self._wait_for_jobs(job_ids, options['wait_seconds'])
            accepted.refresh_from_db()
            rejected.refresh_from_db()
            unrelated.refresh_from_db()

            checks = {
                'accepted_not_pending': accepted.status != 'Pending',
                'rejected_not_pending': rejected.status != 'Pending',
                'rejected_status_matches_possible': rejected.status == rejected.possible_status,
                'unrelated_still_pending': unrelated.status == 'Pending',
                'jobs_completed': not WordSaladRecheckJob.objects.filter(
                    pk__in=job_ids,
                ).exclude(status=WordSaladRecheckJob.STATUS_COMPLETED).exists(),
            }
            result['attempts'] = {
                'accepted': accepted.pk,
                'rejected': rejected.pk,
                'unrelated_pending': unrelated.pk,
            }
            result['jobs'] = sorted(job_ids)
            result['statuses_before_cleanup'] = {
                str(a.pk): a.status for a in (accepted, rejected, unrelated)
            }
            result['checks'] = checks
            if not all(checks.values()):
                raise CommandError('production smoke assertions failed: {}'.format(checks))
        except Exception:
            result['cleanup'] = 'skipped_after_failure'
            self.stdout.write(json.dumps(result, ensure_ascii=False, default=str))
            raise
        else:
            self._cleanup(
                task=task,
                game=game,
                team=team,
                attempts=attempts,
                job_ids=job_ids,
                original_checker_data=original_checker_data,
                state_snapshot=state_snapshot,
                attempt_snapshot=attempt_snapshot,
            )
            result['cleanup'] = 'completed'
            self.stdout.write(json.dumps(result, ensure_ascii=False, default=str))

    def _create_attempts(self, task, game, team, marker):
        rows = (
            (['Лето', 'Макар', 'Град', 'Клин'], '{} accepted'.format(marker)),
            (['Колыван', 'Африка', 'Морда', 'Таль'], '{} rejected'.format(marker)),
            (['Луна', 'Солнце', 'Ворон', 'Глаза'], '{} unrelated'.format(marker)),
        )
        return [
            Attempt.manager.create(
                team=team,
                task=task,
                game=game,
                task_revision=task.attempt_revision,
                text=json.dumps({
                    'words': words,
                    'stage': 'cat_explanation',
                    'explanation': explanation,
                }, ensure_ascii=False),
                status='Pending',
                possible_status='Wrong',
                points=0,
                skip=False,
            )
            for words, explanation in rows
        ]

    def _wait_for_jobs(self, job_ids, wait_seconds):
        deadline = time.monotonic() + wait_seconds
        while True:
            jobs = list(WordSaladRecheckJob.objects.filter(pk__in=job_ids))
            if jobs and all(job.status == WordSaladRecheckJob.STATUS_COMPLETED for job in jobs):
                return
            failed = [job for job in jobs if job.status == WordSaladRecheckJob.STATUS_FAILED]
            if failed:
                raise CommandError('replay job failed: {}'.format([job.pk for job in failed]))
            if time.monotonic() >= deadline:
                raise CommandError('timed out waiting for replay jobs {}'.format(sorted(job_ids)))
            time.sleep(2)

    def _cleanup(
        self, *, task, game, team, attempts, job_ids,
        original_checker_data, state_snapshot, attempt_snapshot,
    ):
        with transaction.atomic():
            task_locked = Task.objects.select_for_update().get(pk=task.pk)
            task_locked.checker_data = original_checker_data
            task_locked.save(
                update_fields=['checker_data'],
                skip_semantics_reconciliation=True,
            )
            Attempt.objects.filter(pk__in=[attempt.pk for attempt in attempts]).delete()
            WordSaladRecheckOutbox.objects.filter(item__job_id__in=job_ids).delete()
            WordSaladRecheckItem.objects.filter(job_id__in=job_ids).delete()
            WordSaladRecheckJob.objects.filter(pk__in=job_ids).delete()

            for pk, values in attempt_snapshot.items():
                Attempt.objects.filter(pk=pk).update(**values)

            current_ids = set(
                ChainTaskState.objects.filter(task=task, game=game, team=team)
                .values_list('pk', flat=True)
            )
            new_ids = current_ids - set(state_snapshot)
            ChainTaskState.objects.filter(pk__in=new_ids).delete()
            for pk, (state, last_attempt_id, updated_at) in state_snapshot.items():
                ChainTaskState.objects.filter(pk=pk).update(
                    state=state,
                    last_attempt_id=last_attempt_id,
                    updated_at=updated_at or timezone.now(),
                )
