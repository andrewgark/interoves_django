"""Cross-instance lock for the minute Telegram cron command."""

from __future__ import annotations

import logging
import socket
from contextlib import contextmanager

from django.db import connection


logger = logging.getLogger('application')

TELEGRAM_CRON_LOCK_NAME = 'interoves:telegram_game_announcements'


@contextmanager
def telegram_cron_lock():
    """Yield whether this invocation owns the MySQL connection-level lock."""
    host = socket.gethostname()
    if connection.vendor != 'mysql':
        # Local/test databases do not share the production multi-instance risk.
        logger.info('Telegram cron lock acquired host=%s backend=%s', host, connection.vendor)
        yield True
        return

    connection.ensure_connection()
    lock_connection = connection.connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT GET_LOCK(%s, 0)', [TELEGRAM_CRON_LOCK_NAME])
        row = cursor.fetchone()
    acquired = bool(row and row[0] == 1)

    if acquired:
        logger.info('Telegram cron lock acquired host=%s', host)
    else:
        logger.info('Telegram cron skipped: lock held host=%s', host)

    try:
        yield acquired
    finally:
        if acquired and connection.connection is not lock_connection:
            # A dropped MySQL connection releases its advisory locks itself.
            logger.warning(
                'Telegram cron lock connection changed before release host=%s',
                host,
            )
        elif acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT RELEASE_LOCK(%s)', [TELEGRAM_CRON_LOCK_NAME])
                    cursor.fetchone()
                logger.info('Telegram cron lock released host=%s', host)
            except Exception:
                # If the connection died, MySQL has already released its locks.
                logger.exception('Telegram cron lock release failed host=%s', host)
