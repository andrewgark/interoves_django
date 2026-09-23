"""Cross-instance lock for the lightweight projection reconciliation cron."""

from contextlib import contextmanager
import logging
import socket

from django.db import connection


logger = logging.getLogger('application')
PROJECTION_CRON_LOCK_NAME = 'interoves:daily_result_projection_reconcile'


@contextmanager
def daily_result_projection_cron_lock():
    """Yield whether this process owns the shared production lock."""
    if connection.vendor != 'mysql':
        yield True
        return
    connection.ensure_connection()
    lock_connection = connection.connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT GET_LOCK(%s, 0)', [PROJECTION_CRON_LOCK_NAME])
        row = cursor.fetchone()
    acquired = bool(row and row[0] == 1)
    logger.info(
        'daily_result_projection_reconcile_lock acquired=%s host=%s',
        acquired, socket.gethostname(),
    )
    try:
        yield acquired
    finally:
        if acquired and connection.connection is lock_connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT RELEASE_LOCK(%s)', [PROJECTION_CRON_LOCK_NAME])
                    cursor.fetchone()
            except Exception:
                logger.exception('daily result projection lock release failed')
