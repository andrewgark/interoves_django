"""SQS events for one anonymous-merge job.

The minute cron keeps scanning jobs that were never published. A published
job is marked in shared Redis so cron does not claim it too.
"""

from __future__ import annotations

import json
import logging
import os
import uuid

import boto3
from django.core.cache import caches
from django.utils import timezone

logger = logging.getLogger('application')

EVENT_TTL_SECONDS = 24 * 60 * 60
QUEUE_URL_ENV = 'ANONYMOUS_MERGE_SQS_QUEUE_URL'


def events_enabled():
    return os.environ.get('ANONYMOUS_MERGE_EVENTS', '') == '1'


def _cache_key(job_id):
    return 'anonymous-merge-event:{}'.format(job_id)


def merge_event_owned(job_id):
    return caches['track_revisions'].get(_cache_key(job_id)) is not None


def mark_merge_event(job_id):
    caches['track_revisions'].set(_cache_key(job_id), '1', timeout=EVENT_TTL_SECONDS)


def clear_merge_event(job_id):
    caches['track_revisions'].delete(_cache_key(job_id))


def publish_anonymous_merge_event(job_id, *, delay_seconds=0):
    """Mark the job and send one named message. Cron must not claim it."""
    if not events_enabled():
        return False
    queue_url = os.environ.get(QUEUE_URL_ENV, '').strip()
    if not queue_url:
        logger.error('anonymous merge event skipped: queue url missing job=%s', job_id)
        return False
    delay = max(0, min(900, int(delay_seconds)))
    mark_merge_event(job_id)
    scheduled_for = timezone.now().isoformat()
    body = {
        'version': 1,
        'type': 'anonymous.merge',
        'run_id': str(uuid.uuid4()),
        'scheduled_for': scheduled_for,
        'dedupe_key': 'anonymous.merge:{}'.format(job_id),
        'payload': {'job_id': str(job_id)},
    }
    try:
        client = boto3.client(
            'sqs',
            region_name=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION', 'eu-central-1'),
        )
        kwargs = {
            'QueueUrl': queue_url,
            'MessageBody': json.dumps(body, separators=(',', ':')),
        }
        if delay:
            kwargs['DelaySeconds'] = delay
        client.send_message(**kwargs)
    except Exception:
        clear_merge_event(job_id)
        logger.exception('anonymous merge event failed job=%s', job_id)
        return False
    logger.info('anonymous merge event published job=%s delay_seconds=%s', job_id, delay)
    return True


def schedule_anonymous_merge_event(job_id):
    from django.db import transaction
    transaction.on_commit(lambda: publish_anonymous_merge_event(job_id))
