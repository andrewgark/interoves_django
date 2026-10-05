"""Repair zero per-answer active times from the timing event ledger."""

from datetime import timedelta
from itertools import groupby

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
            game_id=options['game'], status='Ok', skip=False, replay_slot__isnull=True,
            active_time_ms=0, time__isnull=False,
        ).select_related('task').order_by('pk')
        if options['task_group']:
            qs = qs.filter(task__task_group_id=options['task_group'])

        candidates = repaired = 0
        group_fields = ('game_id', 'task__task_group_id', 'team_id', 'user_id', 'anon_key')
        ordered_qs = qs.order_by(*group_fields, 'time', 'pk')
        for group_key, attempts_iter in groupby(
            ordered_qs.iterator(chunk_size=200),
            key=lambda item: (
                item.game_id, item.task.task_group_id, item.team_id,
                item.user_id, item.anon_key,
            ),
        ):
            game_id, task_group_id, team_id, user_id, anon_key = group_key
            actor_filter = (
                {'team_id': team_id} if team_id is not None else
                {'user_id': user_id} if user_id is not None else
                {'anon_key': anon_key}
            )
            if not any(value is not None and value != '' for value in actor_filter.values()):
                continue
            attempts = list(attempts_iter)
            latest_time = max(attempt.time for attempt in attempts if attempt.time is not None)
            # The attempt is saved before completion_coordinator records the
            # final timing event in the same request. Include only a narrow
            # post-attempt window so that event is recoverable without
            # accidentally consuming a later solve from the same actor.
            event_end = latest_time + timedelta(seconds=30)
            events = DailyTimingEvent.objects.filter(
                game_id=game_id,
                task_group_id=task_group_id,
                replay_slot__isnull=True,
                occurred_at__lte=event_end,
                **actor_filter,
            ).order_by('occurred_at', 'pk')
            events = list(events)
            for attempt in attempts:
                attempt_events = [
                    event for event in events
                    if event.occurred_at <= attempt.time + timedelta(seconds=30)
                ]
                recovered_ms = recovered_timing_ms_from_events(
                    attempt_events,
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
