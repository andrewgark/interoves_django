"""Repair completed daily timers whose snapshot was frozen at zero."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from games.daily.timing import recovered_timing_ms_from_events
from games.models import DailySolveTiming, DailyTimingEvent


class Command(BaseCommand):
    help = 'Repair completed zero-duration daily timers from the timing event ledger (dry-run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--game', required=True)
        parser.add_argument('--task-group', type=int, required=True)
        parser.add_argument('--user', type=int)
        parser.add_argument('--team')
        parser.add_argument('--anon-key')
        parser.add_argument('--apply', action='store_true', help='Persist repairs; omitted means dry-run.')

    def handle(self, *args, **options):
        actor_flags = sum(options[name] is not None for name in ('user', 'team', 'anon_key'))
        if actor_flags > 1:
            raise CommandError('choose only one of --user, --team, --anon-key')

        filters = {
            'game_id': options['game'],
            'task_group_id': options['task_group'],
            'replay_slot__isnull': True,
            'status': DailySolveTiming.STATUS_COMPLETED,
            'frozen_ms': 0,
        }
        if options['user'] is not None:
            filters['user_id'] = options['user']
        elif options['team'] is not None:
            filters['team_id'] = options['team']
        elif options['anon_key'] is not None:
            filters['anon_key'] = options['anon_key']

        rows = DailySolveTiming.objects.filter(**filters).order_by('pk')
        changed = 0
        candidates = 0
        for row in rows:
            actor_filter = (
                {'team_id': row.team_id} if row.team_id is not None else
                {'user_id': row.user_id} if row.user_id is not None else
                {'anon_key': row.anon_key}
            )
            events = DailyTimingEvent.objects.filter(
                game_id=row.game_id,
                task_group_id=row.task_group_id,
                replay_slot__isnull=True,
                **actor_filter,
            ).order_by('occurred_at', 'pk')
            recovered_ms = recovered_timing_ms_from_events(
                events,
                completed_at=row.completed_at,
            )
            self.stdout.write(
                'row={} actor={} recovered_ms={} mode={}'.format(
                    row.pk,
                    actor_filter,
                    recovered_ms,
                    'apply' if options['apply'] else 'dry-run',
                )
            )
            if options['apply'] and recovered_ms > 0:
                with transaction.atomic():
                    locked = DailySolveTiming.objects.select_for_update().get(pk=row.pk)
                    if (
                        locked.status == DailySolveTiming.STATUS_COMPLETED
                        and int(locked.frozen_ms or 0) == 0
                    ):
                        locked.accumulated_ms = recovered_ms
                        locked.frozen_ms = recovered_ms
                        locked.save(update_fields=['accumulated_ms', 'frozen_ms', 'updated_at'])
                        changed += 1
            elif recovered_ms > 0:
                candidates += 1

        self.stdout.write(self.style.SUCCESS(
            '{} row(s) repaired.'.format(changed) if options['apply']
            else 'dry-run complete; {} row(s) would be repaired.'.format(candidates),
        ))
