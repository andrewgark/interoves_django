"""Small, Raddle-specific helpers for safe first-row creation and retries."""

import logging

from django.db import OperationalError, transaction

from games.models import Task


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

    MySQL does not create the conditional UNIQUE constraints Django declares
    for nullable actor columns.  A missing row therefore needs a stable parent
    lock before creation.  Existing rows are locked by primary key, avoiding
    secondary-index/primary-key lock-order inversions.
    """
    row_id = queryset.order_by('pk').values_list('pk', flat=True).first()
    if row_id is not None:
        return queryset.model.objects.select_for_update().get(pk=row_id)

    Task.objects.select_for_update().only('pk').get(pk=task.pk)
    row = queryset.select_for_update().order_by('pk').first()
    if row is not None:
        return row

    if callable(defaults):
        defaults = defaults()
    row = queryset.model.objects.create(**lookup, **defaults)
    return queryset.model.objects.select_for_update().get(pk=row.pk)
