"""Read-only AWS transport snapshots for the support queue dashboard."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from django.core.cache import cache


logger = logging.getLogger('application')

AWS_REGION = os.environ.get('AWS_DEFAULT_REGION') or os.environ.get('AWS_REGION') or 'eu-central-1'
AWS_ACCOUNT_ID = os.environ.get('AWS_ACCOUNT_ID') or '916000456640'
ECS_CLUSTER = 'interoves-workers'
SNAPSHOT_CACHE_KEY = 'support:queue-transport:v1'
SNAPSHOT_CACHE_SECONDS = 20

WORKER_TRANSPORTS = (
    {
        'name': 'identity',
        'label': 'Identity',
        'service': 'interoves-identity-ecs',
        'queue': 'interoves-identity',
        'dlq': 'interoves-identity-dlq',
        'capacity': 'Fargate On-Demand',
    },
    {
        'name': 'background',
        'label': 'Background',
        'service': 'interoves-background-ecs',
        'queue': 'interoves-background',
        'dlq': 'interoves-background-dlq',
        'capacity': 'Fargate Spot',
    },
    {
        'name': 'integrations',
        'label': 'Integrations',
        'service': 'interoves-integrations-ecs',
        'queue': 'interoves-integrations',
        'dlq': 'interoves-integrations-dlq',
        'capacity': 'Fargate On-Demand',
    },
    {
        'name': 'recheck',
        'label': 'Recheck',
        'service': 'interoves-recheck-ecs',
        'queue': 'interoves-recheck',
        'dlq': 'interoves-recheck-dlq',
        'capacity': 'Fargate Spot',
    },
)


def _unknown(reason):
    return {'status': 'unknown', 'reason': reason}


def _error_reason(exc):
    response = getattr(exc, 'response', None) or {}
    error = response.get('Error') or {}
    return error.get('Code') or exc.__class__.__name__


def _count(attributes, name):
    value = attributes.get(name)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _queue_snapshot(client, name):
    try:
        # Construct the URL so the web role only needs GetQueueAttributes on
        # the exact queue ARNs; GetQueueUrl would require a broader resource.
        url = 'https://sqs.{}.amazonaws.com/{}/{}'.format(AWS_REGION, AWS_ACCOUNT_ID, name)
        attributes = client.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=[
                'ApproximateNumberOfMessages',
                'ApproximateNumberOfMessagesNotVisible',
                'ApproximateNumberOfMessagesDelayed',
            ],
        ).get('Attributes', {})
        return {
            'name': name,
            'status': 'ok',
            'visible': _count(attributes, 'ApproximateNumberOfMessages'),
            'in_flight': _count(attributes, 'ApproximateNumberOfMessagesNotVisible'),
            'delayed': _count(attributes, 'ApproximateNumberOfMessagesDelayed'),
        }
    except Exception as exc:  # AWS ClientError and transient network errors
        reason = _error_reason(exc)
        logger.warning('Queue observability read failed queue=%s error=%s', name, reason)
        return {'name': name, **_unknown(reason)}


def _service_snapshot(client, service):
    try:
        response = client.describe_services(cluster=ECS_CLUSTER, services=[service])
        rows = response.get('services') or []
        if not rows:
            return _unknown('service_not_found')
        row = rows[0]
        deployments = row.get('deployments') or []
        primary = next((item for item in deployments if item.get('status') == 'PRIMARY'), {})
        return {
            'status': 'ok',
            'desired': row.get('desiredCount'),
            'running': row.get('runningCount'),
            'pending': row.get('pendingCount'),
            'deployment': primary.get('rolloutState') or primary.get('status') or 'unknown',
            'task_definition': (row.get('taskDefinition') or '').rsplit('/', 1)[-1] or None,
        }
    except Exception as exc:
        reason = _error_reason(exc)
        logger.warning('ECS observability read failed service=%s error=%s', service, reason)
        return _unknown(reason)


def _worker_snapshot(sqs, ecs, definition):
    queue = _queue_snapshot(sqs, definition['queue'])
    dlq = _queue_snapshot(sqs, definition['dlq'])
    service = _service_snapshot(ecs, definition['service'])
    statuses = [queue['status'], dlq['status'], service['status']]
    if 'unknown' in statuses:
        health = 'unknown'
    elif dlq.get('visible', 0):
        health = 'error'
    elif service.get('running') != service.get('desired'):
        health = 'warning'
    else:
        health = 'ok'
    return {
        **definition,
        'health': health,
        'queue_state': queue,
        'dlq_state': dlq,
        'service_state': service,
    }


def transport_snapshot(*, force=False, clients=None):
    """Return a short-lived, JSON-safe read-only snapshot.

    ``clients`` exists for unit tests and never contains credentials in the
    returned payload. AWS failures are represented as ``unknown`` rather than
    as zero, which is important when a role lacks one of the read permissions.
    """
    if not force:
        cached = cache.get(SNAPSHOT_CACHE_KEY)
        if cached is not None:
            return cached
    observed_at = datetime.now(timezone.utc).isoformat()
    if clients is None:
        import boto3

        clients = {
            'sqs': boto3.client('sqs', region_name=AWS_REGION),
            'ecs': boto3.client('ecs', region_name=AWS_REGION),
        }
    try:
        workers = [_worker_snapshot(clients['sqs'], clients['ecs'], definition) for definition in WORKER_TRANSPORTS]
        payload = {
            'status': 'ok' if all(row['health'] != 'unknown' for row in workers) else 'degraded',
            'observed_at': observed_at,
            'cache_seconds': SNAPSHOT_CACHE_SECONDS,
            'workers': workers,
        }
    except Exception as exc:
        logger.exception('Queue transport snapshot failed')
        payload = {
            'status': 'unknown',
            'observed_at': observed_at,
            'cache_seconds': SNAPSHOT_CACHE_SECONDS,
            'workers': [],
            'error': exc.__class__.__name__,
        }
    cache.set(SNAPSHOT_CACHE_KEY, payload, SNAPSHOT_CACHE_SECONDS)
    return payload
