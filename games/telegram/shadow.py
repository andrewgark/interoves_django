"""Phase 2 shadow: decide what Telegram would send, without calling the API.

The live cron remains the sender until cutover.  Shadow must not take
``telegram_game_announcements`` or ``telegram_admin_report`` locks and must
not insert announcement or report rows.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

from django.utils import timezone

_shadow: ContextVar[bool] = ContextVar('telegram_shadow', default=False)
logger = logging.getLogger('application')


def telegram_shadow_active() -> bool:
    return _shadow.get()


@contextmanager
def telegram_shadow():
    token = _shadow.set(True)
    try:
        yield
    finally:
        _shadow.reset(token)


def run_admin_report_live(*, now):
    """Send the admin report for ``now`` (the schedule time, not the worker clock).

    Returns None when the cron lock is already held.
    """
    from games.cron_lock import distributed_cron_lock
    from games.telegram.admin_reports import process_admin_report_tick

    with distributed_cron_lock('telegram_admin_report', ttl_seconds=180) as acquired:
        if not acquired:
            return None
        return process_admin_report_tick(now=now)


def run_announcement_live(*, now):
    """Send announcements. Returns None when the cron lock is already held."""
    from games.telegram.cron_lock import telegram_cron_lock
    from games.telegram.scheduling import process_game_announcements

    with telegram_cron_lock() as acquired:
        if not acquired:
            return None
        return process_game_announcements(now=now)


def run_announcement_shadow(*, now):
    from games.telegram.scheduling import process_game_announcements

    with telegram_shadow():
        return process_game_announcements(now=now)


def run_admin_report_shadow(*, now):
    """Use ``now`` as the schedule time, not the worker clock."""
    from games.telegram.admin_reports import report_periods
    from games.telegram.models import TelegramAdminReport
    from games.telegram.notify import telegram_admin_configured

    scheduled = timezone.localtime(now)
    if scheduled.hour != 0 or not (25 <= scheduled.minute <= 29):
        return {'would_send': 0, 'skipped': 1, 'reason': 'outside_window'}
    if not telegram_admin_configured():
        return {'would_send': 0, 'skipped': 1, 'reason': 'not_configured'}
    kind, current, _comparisons = report_periods(scheduled)
    start, end = current
    if TelegramAdminReport.objects.filter(
        report_type=kind, period_start=start, period_end=end,
    ).exists():
        return {'would_send': 0, 'skipped': 1, 'reason': 'already_sent'}
    logger.info(
        'telegram shadow would send admin report type=%s period_start=%s',
        kind, start.isoformat(),
    )
    return {'would_send': 1, 'skipped': 0, 'reason': 'would_send'}
