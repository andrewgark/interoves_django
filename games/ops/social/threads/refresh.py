"""Keep the long-lived Threads token alive; runs with the daily token-refresh job.

Threads long-lived tokens expire after 60 days. Refreshing a still-valid token
(older than 24h) yields a new 60-day token, which is stored in ThreadsToken.
A token reissued by hand and put into THREADS_ACCESS_TOKEN (SSM) replaces the
DB copy on the next run. The result never includes the token; a held lock
returns None.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from games.ops.cron.lock import distributed_cron_lock
from games.ops.social.threads.api import refresh_access_token, settings_access_token
from games.ops.social.threads.models import ThreadsToken
from games.ops.workers.heartbeat import finish as finish_queue_heartbeat, start as start_queue_heartbeat

logger = logging.getLogger('application')

QUEUE_NAME = 'threads_refresh_token'


def run_threads_token_refresh(*, force=False, max_age_days=30, worker='cron'):
    with distributed_cron_lock(QUEUE_NAME, ttl_seconds=300) as acquired:
        if not acquired:
            return None
        started = start_queue_heartbeat(QUEUE_NAME, worker=worker)
        try:
            result = _refresh_locked(force=force, max_age_days=max_age_days)
        except Exception as exc:
            finish_queue_heartbeat(
                QUEUE_NAME,
                started,
                success=False,
                error='{}: {}'.format(exc.__class__.__name__, exc),
            )
            raise
        failed = result.get('action') == 'failed'
        finish_queue_heartbeat(
            QUEUE_NAME,
            started,
            success=not failed,
            error='refresh failed' if failed else '',
        )
        return result


def _refresh_locked(*, force, max_age_days):
    seed = settings_access_token()
    row = ThreadsToken.get()
    if row is None:
        if not seed:
            return {'action': 'missing_seed'}
        ThreadsToken.objects.create(access_token=seed, seed_token=seed)
        if not force:
            return {'action': 'seeded'}
        row = ThreadsToken.get()
    elif seed and seed != row.seed_token:
        row.access_token = seed
        row.seed_token = seed
        row.expires_at = None
        row.save()
        if not force:
            return {'action': 'reseeded'}

    age_days = (timezone.now() - row.refreshed_at).days
    if not force and age_days < max_age_days:
        return {
            'action': 'skipped',
            'age_days': age_days,
            'max_age_days': max_age_days,
        }

    try:
        payload = refresh_access_token(row.access_token)
    except Exception:
        logger.exception('threads token refresh failed')
        return {'action': 'failed'}

    expires_in = payload.get('expires_in')
    row.access_token = payload['access_token']
    row.expires_at = (
        timezone.now() + timedelta(seconds=int(expires_in)) if expires_in else None
    )
    row.save()
    expires_days = round(int(expires_in) / 86400) if expires_in else None
    return {'action': 'refreshed', 'expires_days': expires_days}
