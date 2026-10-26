"""One Instagram token refresh, shared by the management command and the worker.

The result never includes the token. A missed lock returns None so the
caller can ack without a second refresh.
"""

import logging

from django.conf import settings
from django.utils import timezone

from games.cron_lock import distributed_cron_lock
from games.instagram.api import refresh_and_persist
from games.instagram.models import InstagramToken
from games.queue_heartbeat import finish as finish_queue_heartbeat
from games.queue_heartbeat import start as start_queue_heartbeat

logger = logging.getLogger('application')

QUEUE_NAME = 'instagram_refresh_token'


def run_instagram_token_refresh(*, force=False, max_age_days=30, worker='cron'):
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
    row = InstagramToken.get()
    if row is None:
        seed = (getattr(settings, 'INSTAGRAM_ACCESS_TOKEN', '') or '').strip()
        if not seed:
            return {'action': 'missing_seed'}
        InstagramToken.objects.create(access_token=seed)
        if not force:
            return {'action': 'seeded'}
        row = InstagramToken.get()

    age_days = (timezone.now() - row.refreshed_at).days
    if not force and age_days < max_age_days:
        return {
            'action': 'skipped',
            'age_days': age_days,
            'max_age_days': max_age_days,
        }

    try:
        payload = refresh_and_persist()
    except RuntimeError:
        logger.exception('instagram token refresh failed')
        return {'action': 'failed'}

    expires_in = payload.get('expires_in')
    expires_days = round(int(expires_in) / 86400) if expires_in else None
    return {'action': 'refreshed', 'expires_days': expires_days}
