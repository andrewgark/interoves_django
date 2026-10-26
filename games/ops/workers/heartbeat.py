"""Small DB-backed heartbeat API used by cron-only workers."""

from __future__ import annotations

import os
import socket
import time
from contextlib import contextmanager

from django.utils import timezone

from games.models import QueueWorkerHeartbeat


def _worker_label(worker=None):
    return (worker or 'cron')[:255]


def start(queue_name, *, worker=None):
    now = timezone.now()
    QueueWorkerHeartbeat.objects.update_or_create(
        queue_name=queue_name,
        defaults={
            'status': QueueWorkerHeartbeat.STATUS_RUNNING,
            'worker': _worker_label(worker),
            'host': socket.gethostname()[:255],
            'pid': os.getpid(),
            'started_at': now,
            'finished_at': None,
            'duration_ms': None,
            'processed_count': None,
            'last_error': '',
        },
    )
    return time.monotonic()


def finish(queue_name, started_monotonic, *, success, error='', processed_count=None):
    now = timezone.now()
    updates = dict(
        status=(QueueWorkerHeartbeat.STATUS_SUCCESS if success else QueueWorkerHeartbeat.STATUS_FAILED),
        finished_at=now,
        duration_ms=max(0, int((time.monotonic() - started_monotonic) * 1000)),
        processed_count=processed_count,
        last_error=(str(error) if error else '')[:2000],
        updated_at=now,
    )
    if success:
        updates['last_success_at'] = now
    QueueWorkerHeartbeat.objects.filter(queue_name=queue_name).update(**updates)


def finish_record(queue_name, *, success, error='', processed_count=None):
    now = timezone.now()
    row = QueueWorkerHeartbeat.objects.filter(queue_name=queue_name).first()
    if row is None:
        return
    duration = max(0, int((now - row.started_at).total_seconds() * 1000)) if row.started_at else None
    updates = dict(
        status=(QueueWorkerHeartbeat.STATUS_SUCCESS if success else QueueWorkerHeartbeat.STATUS_FAILED),
        finished_at=now,
        duration_ms=duration,
        processed_count=processed_count,
        last_error=(str(error) if error else '')[:2000],
        updated_at=now,
    )
    if success:
        updates['last_success_at'] = now
    QueueWorkerHeartbeat.objects.filter(queue_name=queue_name).update(**updates)


@contextmanager
def heartbeat(queue_name, *, worker=None):
    started = start(queue_name, worker=worker)
    try:
        yield
    except Exception as exc:
        finish(queue_name, started, success=False, error='{}: {}'.format(exc.__class__.__name__, exc))
        raise
    else:
        finish(queue_name, started, success=True)
