"""Hourly repair and verification for the daily difficulty queue.

The management command and the background worker both call
``run_daily_difficulty_health_check``.  The Redis lock and the 50-minute
``last_health_check_at`` gate keep a second executor from repeating the tick.
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from games.cron_lock import distributed_cron_lock
from games.difficulty import SUPPORTED_GAME_IDS, _supported_placements
from games.difficulty_refresh import (
    DIFFICULTY_REFRESH_LOCK,
    DIFFICULTY_REFRESH_LOCK_TTL_SECONDS,
    due_daily_difficulty_queryset,
    repair_daily_difficulty_queue,
    run_daily_difficulty_refresh,
)
from games.models import DailyDifficultyQueueStatus, DailyGameDifficulty

HEALTH_CHECK_LOCK = 'daily_difficulty_health_check'
HEALTH_CHECK_LOCK_TTL_SECONDS = 180


def run_daily_difficulty_health_check():
    """Return skipped_locked, skipped_recent, healthy, or unhealthy."""
    with distributed_cron_lock(
        HEALTH_CHECK_LOCK,
        ttl_seconds=HEALTH_CHECK_LOCK_TTL_SECONDS,
    ) as acquired:
        if not acquired:
            return {'status': 'skipped_locked'}
        return _run_locked()


def _run_locked():
    now = timezone.now()
    DailyDifficultyQueueStatus.objects.get_or_create(pk=1)
    updated = DailyDifficultyQueueStatus.objects.filter(pk=1).filter(
        Q(last_health_check_at__isnull=True)
        | Q(last_health_check_at__lte=now - timedelta(minutes=50)),
    ).update(last_health_check_at=now)
    if not updated:
        return {'status': 'skipped_recent'}

    problems = []
    repair_report = {'created': 0, 'rescheduled': 0, 'due': 0}
    refresh_skipped = False
    try:
        repair_report = repair_daily_difficulty_queue(now=now)
    except Exception as exc:
        problems.append('queue repair exception: {}: {}'.format(type(exc).__name__, exc))
    # Share the minute worker's lock.  A second calculation after this
    # health-check lock is held can miss a due row and raise a false alert.
    with distributed_cron_lock(
        DIFFICULTY_REFRESH_LOCK,
        ttl_seconds=DIFFICULTY_REFRESH_LOCK_TTL_SECONDS,
    ) as acquired:
        if not acquired:
            refresh_skipped = True
        else:
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
    if never_calculated and not refresh_skipped:
        problems.append('{} published editions never calculated'.format(never_calculated))

    due = due_daily_difficulty_queryset(now=now).count()
    if due and not refresh_skipped:
        problems.append('{} due editions still queued'.format(due))

    failures = published.filter(refresh_fail_count__gt=0).count()
    if failures:
        problems.append('{} published editions with calculation failures'.format(failures))

    status = DailyDifficultyQueueStatus.objects.filter(pk=1).first()
    if not status or not status.last_success_at or status.last_success_at < now - timedelta(hours=2):
        problems.append('no successful worker heartbeat in the last 2 hours')

    if problems:
        detail = '; '.join(problems)
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
        return {
            'status': 'unhealthy',
            'detail': detail,
            'repair': repair_report,
        }

    return {'status': 'healthy', 'repair': repair_report}
