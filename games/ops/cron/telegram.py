"""Compatibility wrapper for the cross-instance Telegram cron lease."""

from contextlib import contextmanager

from games.cron_lock import distributed_cron_lock


TELEGRAM_CRON_LOCK_NAME = 'telegram_game_announcements'


@contextmanager
def telegram_cron_lock():
    with distributed_cron_lock(TELEGRAM_CRON_LOCK_NAME, ttl_seconds=300) as acquired:
        yield acquired
