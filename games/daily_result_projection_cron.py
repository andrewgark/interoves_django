"""Compatibility wrapper for the projection reconciliation cron lease."""

from contextlib import contextmanager

from games.cron_lock import distributed_cron_lock


PROJECTION_CRON_LOCK_NAME = 'daily_result_projection_reconcile'


@contextmanager
def daily_result_projection_cron_lock():
    with distributed_cron_lock(PROJECTION_CRON_LOCK_NAME, ttl_seconds=120) as acquired:
        yield acquired
