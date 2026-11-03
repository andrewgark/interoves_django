"""Bounded deletion of expired ``django_session`` rows.

Django's ``clearsessions`` issues one unbounded ``DELETE``; on production RDS
that would lock and scan hundreds of thousands of rows at once. This removes
expired rows in small primary-key batches with a row and time budget, so the
hourly background tick drains any backlog gradually and then keeps up.
"""

import time

from django.contrib.sessions.models import Session
from django.utils import timezone

SESSION_CLEANUP_LOCK_NAME = 'expired_session_cleanup'
SESSION_CLEANUP_LOCK_TTL_SECONDS = 300
SESSION_CLEANUP_BATCH_SIZE = 2000
SESSION_CLEANUP_MAX_BATCHES = 50
SESSION_CLEANUP_TIME_BUDGET_SECONDS = 60.0


def delete_expired_sessions(
    *,
    batch_size=SESSION_CLEANUP_BATCH_SIZE,
    max_batches=SESSION_CLEANUP_MAX_BATCHES,
    time_budget_seconds=SESSION_CLEANUP_TIME_BUDGET_SECONDS,
):
    """Delete up to ``batch_size * max_batches`` expired sessions.

    Returns ``{'deleted': n, 'complete': bool}``; ``complete`` is false when a
    budget ran out before the expired backlog was empty.
    """
    cutoff = timezone.now()
    deadline = time.monotonic() + time_budget_seconds
    deleted = 0
    for _ in range(max_batches):
        keys = list(
            Session.objects.filter(expire_date__lt=cutoff)
            .order_by()
            .values_list('session_key', flat=True)[:batch_size]
        )
        if not keys:
            return {'deleted': deleted, 'complete': True}
        # Re-check expiry so a session refreshed between SELECT and DELETE survives.
        deleted += Session.objects.filter(
            session_key__in=keys, expire_date__lt=cutoff,
        ).delete()[0]
        if len(keys) < batch_size:
            return {'deleted': deleted, 'complete': True}
        if time.monotonic() >= deadline:
            break
    return {'deleted': deleted, 'complete': False}
