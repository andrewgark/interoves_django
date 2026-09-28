"""Small, Raddle-specific helpers for safe first-row creation and retries."""

import logging

from django.db import IntegrityError, OperationalError, transaction

MYSQL_DEADLOCK_ERRNO = 1213
RADDLE_DEADLOCK_ATTEMPTS = 3

logger = logging.getLogger(__name__)


def _mysql_errno(exc):
    """Return a MySQL errno from an OperationalError and its cause chain."""
    current = exc
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        args = getattr(current, 'args', ())
        if args and isinstance(args[0], int):
            return args[0]
        current = getattr(current, '__cause__', None)
    return None


def is_mysql_deadlock(exc):
    return _mysql_errno(exc) == MYSQL_DEADLOCK_ERRNO


def run_raddle_atomic_with_deadlock_retry(operation, *, label):
    """Run one small transaction, retrying only MySQL deadlocks."""
    for attempt in range(1, RADDLE_DEADLOCK_ATTEMPTS + 1):
        try:
            with transaction.atomic():
                return operation()
        except OperationalError as exc:
            if not is_mysql_deadlock(exc):
                raise
            if attempt >= RADDLE_DEADLOCK_ATTEMPTS:
                logger.warning(
                    'raddle deadlock exhausted attempts=%s label=%s',
                    attempt, label,
                )
                raise
            logger.warning(
                'raddle deadlock retry attempt=%s/%s label=%s',
                attempt, RADDLE_DEADLOCK_ATTEMPTS, label,
            )


def lock_or_create_raddle_state(*, queryset, task, lookup, defaults):
    """Return a locked state row, serializing only first-row creation.

    The state models have a non-null actor-context unique key
    (``actor_key``/``namespace_key`` or ``actor_key``/``replay_slot_key``).
    Let that key arbitrate concurrent first-row creation instead of locking the
    parent Task.  The parent lock serialized every actor on the same task and
    made UI autosaves contend with answer submissions for up to InnoDB's lock
    wait timeout.  Existing rows are locked by primary key, avoiding
    secondary-index/primary-key lock-order inversions.
    """
    row_id = queryset.order_by('pk').values_list('pk', flat=True).first()
    if row_id is not None:
        return queryset.model.objects.select_for_update().get(pk=row_id)

    if callable(defaults):
        defaults = defaults()
    try:
        # Keep the duplicate-key failure inside a savepoint so callers can
        # continue using their surrounding transaction after another request
        # wins the first-row race.
        with transaction.atomic():
            row = queryset.model.objects.create(**lookup, **defaults)
    except IntegrityError:
        row_id = queryset.order_by('pk').values_list('pk', flat=True).first()
        if row_id is None:
            raise
        return queryset.model.objects.select_for_update().get(pk=row_id)
    return queryset.model.objects.select_for_update().get(pk=row.pk)
