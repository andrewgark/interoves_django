"""Run ordering-sensitive PendingAttempt smoke tests against production."""

from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from games.attempt_admin_bulk import accept_pending_attempts, reject_pending_attempts
from games.models import (
    Attempt,
    ChainTaskState,
    Game,
    Task,
    Team,
    WordSaladRecheckItem,
    WordSaladRecheckJob,
    WordSaladRecheckOutbox,
)
from games.recheck import recheck_chain_task


class Command(BaseCommand):
    help = 'Run ordering-sensitive live smoke tests for PendingAttempt ДА/НЕТ.'

    def add_arguments(self, parser):
        parser.add_argument('--task', type=int, required=True)
        parser.add_argument('--game', required=True)
        parser.add_argument('--team', default='admin')
        parser.add_argument('--wait-seconds', type=int, default=300)
        parser.add_argument('--confirm-production', action='store_true')

    def handle(self, *args, **options):
        if not options['confirm_production']:
            raise CommandError('pass --confirm-production to write and clean production data')
        if not 1 <= options['wait_seconds'] <= 900:
            raise CommandError('--wait-seconds must be between 1 and 900')

        task = Task.objects.get(pk=options['task'])
        game = Game.objects.get(pk=options['game'])
        team = Team.objects.get(pk=options['team'])
        if task.task_type != 'wall':
            raise CommandError('this smoke test currently supports wall tasks only')
        if WordSaladRecheckJob.objects.filter(
            task=task, game=game,
            status__in=(WordSaladRecheckJob.STATUS_PENDING, WordSaladRecheckJob.STATUS_RUNNING),
        ).exists():
            raise CommandError('an active replay job already exists for this task/game')

        baseline_checker_data = task.checker_data
        baseline_states = {
            row.pk: (row.state, row.last_attempt_id, row.updated_at)
            for row in ChainTaskState.objects.filter(task=task, game=game, team=team)
        }
        baseline_attempts = {
            row.pk: {
                field: getattr(row, field)
                for field in ('status', 'possible_status', 'points', 'state', 'comment', 'skip')
            }
            for row in Attempt.manager.filter(task=task, game=game, team=team)
        }
        marker = 'PROD-ORDER-TEST-{}'.format(uuid.uuid4().hex[:12])
        scenarios = []
        created_attempt_ids = set()
        created_job_ids = set()

        try:
            scenario = self._same_category(task, game, team, marker, options['wait_seconds'])
            scenarios.append(scenario)
            created_job_ids.update(scenario['jobs'])
            self._reset(task, game, team, marker, created_attempt_ids, created_job_ids, baseline_checker_data)
            scenario = self._different_category(task, game, team, marker, options['wait_seconds'])
            scenarios.append(scenario)
            created_job_ids.update(scenario['jobs'])
            self._reset(task, game, team, marker, created_attempt_ids, created_job_ids, baseline_checker_data)
            scenario = self._bulk_one_job(task, game, team, marker, options['wait_seconds'])
            scenarios.append(scenario)
            created_job_ids.update(scenario['jobs'])
        finally:
            self._cleanup(
                task=task,
                game=game,
                team=team,
                marker=marker,
                attempt_ids=created_attempt_ids,
                job_ids=created_job_ids,
                checker_data=baseline_checker_data,
                baseline_attempts=baseline_attempts,
                baseline_states=baseline_states,
            )

        self.stdout.write(json.dumps({
            'marker': marker,
            'scenarios': scenarios,
            'cleanup': 'completed',
        }, ensure_ascii=False, default=str))

    def _categories(self, task):
        payload = json.loads(task.checker_data or '{}')
        categories = payload.get('answers') or []
        if len(categories) < 2:
            raise CommandError('wall checker must have at least two categories')
        return categories

    def _create_pair(self, task, game, team, marker, same_category, distinct_actors=False):
        categories = self._categories(task)
        first_category = categories[0]
        second_category = first_category if same_category else categories[1]
        created = []
        for index, category in enumerate((first_category, second_category)):
            row = Attempt.manager.create(
                team=team,
                task=task,
                game=game,
                task_revision=task.attempt_revision,
                text=json.dumps({
                    'words': category['words'],
                    'stage': 'cat_explanation',
                    'explanation': '{}-{}'.format(marker, index),
                }, ensure_ascii=False),
                status='Pending',
                possible_status='Wrong',
                points=0,
                state='{}',
                skip=False,
                anon_key='{}-actor-{}'.format(marker, index) if distinct_actors else None,
            )
            created.append(row)
        Attempt.manager.filter(pk=created[0].pk).update(time=timezone.now() - timedelta(seconds=2))
        Attempt.manager.filter(pk=created[1].pk).update(time=timezone.now() - timedelta(seconds=1))
        return created

    def _same_category(self, task, game, team, marker, wait_seconds):
        early, late = self._create_pair(task, game, team, marker + '-same', True)
        reject_pending_attempts([late.pk])
        receipt = accept_pending_attempts([early.pk], return_receipt=True)
        jobs = self._new_jobs(receipt)
        self._wait(jobs, wait_seconds)
        early.refresh_from_db()
        late.refresh_from_db()
        checks = {
            'early_not_pending': early.status != 'Pending',
            'late_rejected_stays_not_pending': late.status == 'Wrong',
            # A resolved attempt may have its hidden checker result refreshed
            # during replay.  The invariant we need here is that its explicit
            # NO decision remains resolved, never that possible_status is
            # frozen at the pre-replay value.
            'late_possible_status_after_replay': late.possible_status,
        }
        if not all(
            value is True or key == 'late_possible_status_after_replay'
            for key, value in checks.items()
        ):
            raise CommandError('same-category ordering failed: {}'.format(checks))
        return {'name': 'same_category_reject_late_accept_early', 'checks': checks, 'jobs': jobs}

    def _different_category(self, task, game, team, marker, wait_seconds):
        early, late = self._create_pair(task, game, team, marker + '-different', False)
        reject_pending_attempts([late.pk])
        receipt = accept_pending_attempts([early.pk], return_receipt=True)
        jobs = self._new_jobs(receipt)
        self._wait(jobs, wait_seconds)
        early.refresh_from_db()
        late.refresh_from_db()
        checks = {
            'early_not_pending': early.status != 'Pending',
            'different_category_late_rejected_stays_not_pending': late.status == 'Wrong',
        }
        if not all(checks.values()):
            raise CommandError('different-category ordering failed: {}'.format(checks))
        return {'name': 'different_category_reject_late_accept_early', 'checks': checks, 'jobs': jobs}

    def _bulk_one_job(self, task, game, team, marker, wait_seconds):
        first, second = self._create_pair(
            task, game, team, marker + '-bulk', False, distinct_actors=True,
        )
        receipt = accept_pending_attempts([first.pk, second.pk], return_receipt=True)
        jobs = self._receipt_jobs(receipt)
        receipt_jobs = receipt['queue_receipt']['jobs']
        checks = {
            'receipt_has_jobs': bool(jobs),
            'receipt_has_bulk_job': any(
                row.get('total_items', 0) >= 2 for row in receipt_jobs
            ),
        }
        if not all(checks.values()):
            raise CommandError('bulk receipt failed: {}'.format(checks))
        self._wait(jobs, wait_seconds)
        first.refresh_from_db()
        second.refresh_from_db()
        checks.update({
            'first_not_pending': first.status != 'Pending',
            'second_not_pending': second.status != 'Pending',
        })
        if not all(checks.values()):
            raise CommandError('bulk replay failed: {}'.format(checks))
        return {
            'name': 'bulk_two_accepts_one_job',
            'checks': checks,
            'receipt_counts': {
                key: receipt['queue_receipt'][key]
                for key in ('new_jobs', 'existing_jobs', 'new_items', 'existing_items')
            },
            'jobs': jobs,
        }

    @staticmethod
    def _new_jobs(receipt):
        return [row['id'] for row in receipt['queue_receipt']['jobs'] if row.get('created')]

    @staticmethod
    def _receipt_jobs(receipt):
        return [row['id'] for row in receipt['queue_receipt']['jobs']]

    def _wait(self, job_ids, wait_seconds):
        if not job_ids:
            raise CommandError('scenario did not create a replay job')
        deadline = time.monotonic() + wait_seconds
        while True:
            jobs = list(WordSaladRecheckJob.objects.filter(pk__in=job_ids))
            if jobs and all(job.status == WordSaladRecheckJob.STATUS_COMPLETED for job in jobs):
                return
            failed = [job for job in jobs if job.status == WordSaladRecheckJob.STATUS_FAILED]
            if failed:
                raise CommandError('replay job failed: {}'.format([job.pk for job in failed]))
            if time.monotonic() >= deadline:
                raise CommandError('timed out waiting for jobs {}'.format(job_ids))
            time.sleep(2)

    def _reset(self, task, game, team, marker, attempt_ids, job_ids, checker_data):
        self._cleanup_records(marker, attempt_ids, job_ids)
        task.checker_data = checker_data
        task.save(update_fields=['checker_data'], skip_semantics_reconciliation=True)
        recheck_chain_task(task, team=team, game=game, notify=False)

    def _cleanup(self, **kwargs):
        self._cleanup_records(kwargs['marker'], kwargs['attempt_ids'], kwargs['job_ids'])
        task = kwargs['task']
        task.checker_data = kwargs['checker_data']
        task.save(update_fields=['checker_data'], skip_semantics_reconciliation=True)
        for pk, values in kwargs['baseline_attempts'].items():
            Attempt.manager.filter(pk=pk).update(**values)
        current_ids = set(ChainTaskState.objects.filter(
            task=task, game=kwargs['game'], team=kwargs['team'],
        ).values_list('pk', flat=True))
        new_ids = current_ids - set(kwargs['baseline_states'])
        ChainTaskState.objects.filter(pk__in=new_ids).delete()
        for pk, (state, last_attempt_id, updated_at) in kwargs['baseline_states'].items():
            ChainTaskState.objects.filter(pk=pk).update(
                state=state, last_attempt_id=last_attempt_id, updated_at=updated_at or timezone.now(),
            )

    @staticmethod
    def _cleanup_records(marker, attempt_ids, job_ids):
        marker_ids = list(Attempt.manager.filter(text__contains=marker).values_list('pk', flat=True))
        with transaction.atomic():
            WordSaladRecheckOutbox.objects.filter(item__job_id__in=job_ids).delete()
            WordSaladRecheckItem.objects.filter(job_id__in=job_ids).delete()
            WordSaladRecheckJob.objects.filter(pk__in=job_ids).delete()
            Attempt.manager.filter(pk__in=set(attempt_ids) | set(marker_ids)).delete()
