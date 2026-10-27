"""Helpers for retrying complete MySQL transactions after lock conflicts."""

import time
from contextlib import contextmanager

from django.db import DatabaseError, OperationalError, connection

MYSQL_DEADLOCK_ERRNO = 1213
MYSQL_LOCK_WAIT_TIMEOUT_ERRNO = 1205
MYSQL_RETRYABLE_ERRNOS = frozenset((
    MYSQL_DEADLOCK_ERRNO,
    MYSQL_LOCK_WAIT_TIMEOUT_ERRNO,
))


@contextmanager
def bounded_lock_wait(seconds):
    """Cap InnoDB row-lock waits on this connection for the enclosed block.

    MySQL waits 50 s by default. A request that a long transaction elsewhere
    keeps waiting outlives its client and holds one of the few ASGI threads;
    a short wait fails with errno 1205 instead, which callers retry or map
    to 503. The session goes back to the server default afterwards, in case
    the connection is reused by a later request.
    """
    if connection.vendor != 'mysql':
        yield
        return
    with connection.cursor() as cursor:
        cursor.execute('SET SESSION innodb_lock_wait_timeout = %s', [int(seconds)])
    try:
        yield
    finally:
        if connection.is_usable():
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SET SESSION innodb_lock_wait_timeout = DEFAULT')
            except DatabaseError:
                pass


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
