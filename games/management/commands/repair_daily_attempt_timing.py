"""Repair zero per-answer active times from the timing event ledger."""

from django.core.management.base import BaseCommand
from django.db import transaction

from games.daily.timing import recovered_timing_ms_from_events
from games.models import Attempt, DailyTimingEvent


class Command(BaseCommand):
    help = 'Repair zero active_time_ms attempts from server timing events (dry-run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--game', required=True)
        parser.add_argument('--task-group', type=int)
        parser.add_argument('--apply', action='store_true', help='Persist repairs; omitted means dry-run.')

    def handle(self, *args, **options):
        qs = Attempt.manager.filter(
            game_id=options['game'], skip=False, replay_slot__isnull=True,
            active_time_ms=0,
        ).select_related('task').order_by('pk')
        if options['task_group']:
            qs = qs.filter(task__task_group_id=options['task_group'])

        candidates = repaired = 0
        for attempt in qs.iterator(chunk_size=200):
            actor_filter = (
                {'team_id': attempt.team_id} if attempt.team_id is not None else
                {'user_id': attempt.user_id} if attempt.user_id is not None else
                {'anon_key': attempt.anon_key}
            )
            if not any(value is not None and value != '' for value in actor_filter.values()):
                continue
            events = DailyTimingEvent.objects.filter(
                game_id=attempt.game_id,
                task_group_id=attempt.task.task_group_id,
                replay_slot__isnull=True,
                occurred_at__lte=attempt.time,
                **actor_filter,
            ).order_by('occurred_at', 'pk')
            recovered_ms = recovered_timing_ms_from_events(
                events,
                completed_at=attempt.time,
            )
            if recovered_ms <= 0:
                continue
            candidates += 1
            self.stdout.write(
                'attempt={} old=0 recovered_ms={} mode={}'.format(
                    attempt.pk, recovered_ms, 'apply' if options['apply'] else 'dry-run',
                )
            )
            if options['apply']:
                with transaction.atomic():
                    updated = Attempt.manager.filter(pk=attempt.pk, active_time_ms=0).update(
                        active_time_ms=recovered_ms,
                    )
                repaired += updated

        self.stdout.write(self.style.SUCCESS(
            '{} attempt(s) repaired.'.format(repaired) if options['apply']
            else 'dry-run complete; {} attempt(s) would be repaired.'.format(candidates),
        ))
