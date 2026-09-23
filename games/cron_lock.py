"""Cross-instance leases for scheduled management commands.

The cron entries are installed on every Elastic Beanstalk instance.  A local
``flock`` therefore prevents overlap on one host only; this lease makes the
logical job single-flight across the environment without consuming a MySQL
connection on losing invocations.
"""

from contextlib import contextmanager
import logging
import socket
import time
import uuid

from django.core.cache import caches
from django.db import connection


logger = logging.getLogger('application')
_RELEASE_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)


def _redis_client_and_key(lock_name):
    cache = caches['track_revisions']
    backend = getattr(cache, '_cache', None)
    client_factory = getattr(backend, 'get_client', None)
    if client_factory is None:
        return None, None
    # Use the configured RedisCache pool and key prefix.  The low-level client
    # is needed only for compare-and-delete release; add()/delete() alone can
    # delete a newer owner's lease after this process's TTL expires.
    return client_factory(write=True), cache.make_key('cron-lock:' + lock_name)


@contextmanager
def distributed_cron_lock(lock_name, *, ttl_seconds):
    """Yield whether this invocation owns ``lock_name``.

    Production with Redis configured fails closed if the lock backend cannot
    be reached: the next minute/hourly tick can retry, while duplicate work is
    never silently enabled.  Local non-Redis test databases retain the old
    single-process behaviour.
    """
    host = socket.gethostname()
    started = time.perf_counter()
    client = key = token = None
    acquired = False
    try:
        client, key = _redis_client_and_key(lock_name)
        if client is None:
            if connection.vendor != 'mysql':
                acquired = True
            else:
                logger.error(
                    'cron_lock_failed job=%s host=%s reason=redis_unconfigured',
                    lock_name, host,
                )
        else:
            token = uuid.uuid4().hex.encode('ascii')
            acquired = bool(client.set(key, token, nx=True, ex=int(ttl_seconds)))
    except Exception:
        logger.exception('cron_lock_failed job=%s host=%s reason=redis_error', lock_name, host)

    if not acquired:
        logger.info('cron_lock_skipped job=%s host=%s', lock_name, host)
        yield False
        return

    logger.info(
        'cron_lock_acquired job=%s host=%s ttl_seconds=%s',
        lock_name, host, ttl_seconds,
    )
    try:
        yield True
    except Exception:
        logger.exception(
            'cron_failed job=%s host=%s duration_ms=%.1f',
            lock_name, host, (time.perf_counter() - started) * 1000,
        )
        raise
    else:
        logger.info(
            'cron_finished job=%s host=%s duration_ms=%.1f',
            lock_name, host, (time.perf_counter() - started) * 1000,
        )
    finally:
        if client is not None and key is not None and token is not None:
            try:
                client.eval(_RELEASE_SCRIPT, 1, key, token)
            except Exception:
                logger.exception('cron_lock_release_failed job=%s host=%s', lock_name, host)
