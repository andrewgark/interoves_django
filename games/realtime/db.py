"""Helpers for retrying complete MySQL transactions after lock conflicts."""

import time

from django.db import OperationalError


MYSQL_DEADLOCK_ERRNO = 1213
MYSQL_LOCK_WAIT_TIMEOUT_ERRNO = 1205
MYSQL_RETRYABLE_ERRNOS = frozenset((
    MYSQL_DEADLOCK_ERRNO,
    MYSQL_LOCK_WAIT_TIMEOUT_ERRNO,
))


def mysql_errno(exc):
    current = exc
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        args = getattr(current, 'args', ())
        if args:
            try:
                return int(args[0])
            except (TypeError, ValueError):
                pass
        current = getattr(current, '__cause__', None)
    return None


def is_mysql_retryable_lock_error(exc):
    return isinstance(exc, OperationalError) and mysql_errno(exc) in MYSQL_RETRYABLE_ERRNOS


def log_lock_retry(logger, *, label, attempt, max_attempts, exc):
    logger.warning(
        'mysql lock retry attempt=%s/%s label=%s errno=%s',
        attempt, max_attempts, label, mysql_errno(exc),
    )
    time.sleep(min(0.25, 0.05 * (2 ** max(0, attempt - 1))))
