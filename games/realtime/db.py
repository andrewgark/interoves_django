"""Helpers for retrying complete MySQL transactions after lock conflicts."""

import logging
import time
from contextlib import contextmanager

from django.db import DatabaseError, IntegrityError, OperationalError, connection, transaction

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


LOCK_RETRY_ATTEMPTS = 3

# Legacy logger name (these helpers started in the Raddle package); production
# log searches and tests rely on it.
state_lock_logger = logging.getLogger('games.raddle_concurrency')


def run_atomic_with_lock_retry(operation, *, label):
    """Run one small transaction, retrying MySQL deadlocks and lock wait timeouts."""
    for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
        try:
            with transaction.atomic():
                return operation()
        except OperationalError as exc:
            if not is_mysql_retryable_lock_error(exc):
                raise
            if attempt >= LOCK_RETRY_ATTEMPTS:
                state_lock_logger.warning(
                    'raddle lock retry exhausted attempts=%s label=%s errno=%s',
                    attempt, label, mysql_errno(exc),
                )
                raise
            log_lock_retry(
                state_lock_logger,
                label=label,
                attempt=attempt,
                max_attempts=LOCK_RETRY_ATTEMPTS,
                exc=exc,
            )


def lock_or_create_state_row(*, queryset, task, lookup, defaults):
    """Return a locked state row, serializing only first-row creation.

    The state models have a non-null actor-context unique key
    (``actor_key``/``namespace_key`` or ``actor_key``/``replay_slot_key``).
    Let that key arbitrate concurrent first-row creation instead of locking the
    parent Task.  The parent lock serialized every actor on the same task and
    made UI autosaves contend with answer submissions for up to InnoDB's lock
    wait timeout.  Existing rows are locked by primary key, avoiding
    secondary-index/primary-key lock-order inversions.
    """
    del task
    # A locking read is important here.  A normal read can use a stale
    # REPEATABLE READ snapshot after another transaction wins the insert race.
    row = queryset.select_for_update().order_by('pk').first()
    if row is not None:
        return row

    if callable(defaults):
        defaults = defaults()
    try:
        # Keep the duplicate-key failure inside a savepoint so callers can
        # continue using their surrounding transaction after another request
        # wins the first-row race.
        with transaction.atomic():
            row = queryset.model.objects.create(**lookup, **defaults)
    except IntegrityError:
        # select_for_update() is a current read on MySQL and therefore sees
        # the committed row that caused the duplicate-key error.
        row = queryset.select_for_update().order_by('pk').first()
        if row is None:
            raise
        return row
    return queryset.model.objects.select_for_update().get(pk=row.pk)
