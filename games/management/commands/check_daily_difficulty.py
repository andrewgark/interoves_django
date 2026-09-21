"""Check and recover the daily-game difficulty queue."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from games.difficulty import SUPPORTED_GAME_IDS, _supported_placements
from games.difficulty_refresh import (
    due_daily_difficulty_queryset,
    repair_daily_difficulty_queue,
    run_daily_difficulty_refresh,
)
from games.models import DailyDifficultyQueueStatus, DailyGameDifficulty


class Command(BaseCommand):
    help = 'Repair and verify the daily-game difficulty refresh queue.'

    def handle(self, *args, **options):
        now = timezone.now()
        DailyDifficultyQueueStatus.objects.get_or_create(pk=1)
        acquired = DailyDifficultyQueueStatus.objects.filter(pk=1).filter(
            Q(last_health_check_at__isnull=True)
            | Q(last_health_check_at__lte=now - timedelta(minutes=50)),
        ).update(last_health_check_at=now)
        if not acquired:
            self.stdout.write('Hourly difficulty check already ran recently; skipping.')
            return

        problems = []
        repair_report = {'created': 0, 'rescheduled': 0, 'due': 0}
        try:
            repair_report = repair_daily_difficulty_queue(now=now)
        except Exception as exc:
            problems.append('queue repair exception: {}: {}'.format(type(exc).__name__, exc))
        try:
            run_daily_difficulty_refresh(limit=100, worker='hourly-healthcheck')
        except Exception as exc:
            problems.append('worker exception: {}: {}'.format(type(exc).__name__, exc))

        now = timezone.now()
        missing = [
            placement.pk for placement in _supported_placements()
            if not DailyGameDifficulty.objects.filter(placement_id=placement.pk).exists()
        ]
        if missing:
            problems.append('missing snapshots for {} placements'.format(len(missing)))

        published = DailyGameDifficulty.objects.filter(
            placement__game_id__in=SUPPORTED_GAME_IDS,
            published_at__lte=now,
        )
        never_calculated = published.filter(calculated_at__isnull=True).count()
        if never_calculated:
            problems.append('{} published editions never calculated'.format(never_calculated))

        due = due_daily_difficulty_queryset(now=now).count()
        if due:
            problems.append('{} due editions still queued'.format(due))

        failures = published.filter(refresh_fail_count__gt=0).count()
        if failures:
            problems.append('{} published editions with calculation failures'.format(failures))

        # A command that starts but never completes is also an unhealthy worker.
        status = DailyDifficultyQueueStatus.objects.filter(pk=1).first()
        if not status or not status.last_success_at or status.last_success_at < now - timedelta(hours=2):
            problems.append('no successful worker heartbeat in the last 2 hours')

        if problems:
            detail = '; '.join(problems)
            self.stderr.write(self.style.ERROR('Daily difficulty unhealthy: ' + detail))
            from games.telegram.notify import send_admin_message
            send_admin_message(
                '⚠️ Не работает подсчёт сложности ежедневных заданий.\n'
                'Автовосстановление не устранило проблему.\n'
                'Причины: {}\n'
                'Очередь: создано {}, исправлено сроков {}, готово к обработке {}.'.format(
                    detail,
                    repair_report['created'],
                    repair_report['rescheduled'],
                    repair_report['due'],
                ),
                force=True,
            )
            return

        self.stdout.write(self.style.SUCCESS(
            'Daily difficulty healthy; recovery tick processed successfully.'
        ))
