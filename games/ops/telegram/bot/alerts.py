"""Asynchronous Telegram admin alerts published to the integrations worker."""

from __future__ import annotations

import json
import logging
import os
import uuid

import boto3
from botocore.config import Config
from django.utils import timezone

from games.ops.workers.messages import TELEGRAM_ADMIN_ALERT

logger = logging.getLogger('application')

QUEUE_URL_ENV = 'INTEGRATIONS_SQS_QUEUE_URL'


def enqueue_integrations_message(
    *, message_type: str, payload: dict, dedupe_key: str, label: str, delay_seconds: int = 0,
) -> bool:
    """Put one message on the integrations queue; never block a web request on Telegram."""
    queue_url = os.environ.get(QUEUE_URL_ENV, '').strip()
    if not queue_url:
        logger.error(
            'event=telegram_admin_alert_enqueue_failed reason=queue_url_missing alert=%s',
            label,
        )
        return False
    body = {
        'version': 1,
        'type': message_type,
        'run_id': str(uuid.uuid4()),
        'scheduled_for': timezone.now().isoformat(),
        'dedupe_key': dedupe_key,
        'payload': payload,
    }
    try:
        client = boto3.client(
            'sqs',
            region_name=os.environ.get('AWS_REGION') or os.environ.get(
                'AWS_DEFAULT_REGION', 'eu-central-1',
            ),
            config=Config(connect_timeout=1, read_timeout=2, retries={'max_attempts': 1}),
        )
        message = {
            'QueueUrl': queue_url,
            'MessageBody': json.dumps(body, separators=(',', ':')),
        }
        if delay_seconds > 0:
            # SQS caps per-message delay at 15 minutes.
            message['DelaySeconds'] = min(int(delay_seconds), 900)
        client.send_message(**message)
    except Exception:
        logger.exception(
            'event=telegram_admin_alert_enqueue_failed alert=%s',
            label,
        )
        return False
    logger.info(
        'event=telegram_admin_alert_enqueued alert=%s',
        label,
    )
    return True


def publish_admin_alert(*, alert: str, payload: dict, dedupe_key: str = '') -> bool:
    """Publish an admin alert; Telegram delivery stays outside the web process."""
    alert_payload = dict(payload)
    alert_payload['alert'] = str(alert)
    return enqueue_integrations_message(
        message_type=TELEGRAM_ADMIN_ALERT,
        payload=alert_payload,
        dedupe_key=dedupe_key or 'telegram.admin_alert:{}'.format(alert),
        label=alert,
    )


def publish_word_salad_submission_error(*, game_id: str, incident_id: str) -> bool:
    return publish_admin_alert(
        alert='word_salad_submission',
        dedupe_key='telegram.admin_alert:word_salad_submission:{}:{}'.format(
            game_id, incident_id,
        ),
        payload={'game_id': str(game_id), 'incident_id': str(incident_id)},
    )
