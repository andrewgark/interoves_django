"""Minimal envelope for scheduled background SQS messages."""

import json
from datetime import timedelta

from django.utils.dateparse import parse_datetime

MESSAGE_VERSION = 1
DIFFICULTY_REFRESH = 'difficulty.refresh'
DIFFICULTY_HEALTH_CHECK = 'difficulty.health_check'
TELEGRAM_ANNOUNCEMENTS = 'telegram.announcements'
TELEGRAM_ADMIN_REPORT = 'telegram.admin_report'
PROJECTION_RECONCILE = 'projection.reconcile'
PROJECTION_REFRESH = 'projection.refresh'
ANONYMOUS_MERGE = 'anonymous.merge'
ANONYMOUS_MERGE_RECONCILE = 'anonymous.merge_reconcile'
INSTAGRAM_TOKEN_REFRESH = 'instagram.token_refresh'
SOCIAL_PUBLISH = 'social.publish'
STALE_AFTER = timedelta(minutes=2)
ANNOUNCEMENT_STALE_AFTER = timedelta(minutes=3)
ADMIN_REPORT_STALE_AFTER = timedelta(minutes=20)
INSTAGRAM_STALE_AFTER = timedelta(hours=12)
SOCIAL_STALE_AFTER = timedelta(minutes=3)


class InvalidBackgroundMessage(ValueError):
    pass


def parse_background_message(body):
    """Return a normalized envelope or raise InvalidBackgroundMessage."""
    if not isinstance(body, (bytes, bytearray)):
        raise InvalidBackgroundMessage('body must be bytes')
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise InvalidBackgroundMessage('body is not utf-8') from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidBackgroundMessage('body is not json') from exc
    if not isinstance(payload, dict):
        raise InvalidBackgroundMessage('body must be a json object')
    if payload.get('version') != MESSAGE_VERSION:
        raise InvalidBackgroundMessage('unsupported version')
    message_type = payload.get('type')
    if not isinstance(message_type, str) or not message_type:
        raise InvalidBackgroundMessage('type is required')
    scheduled_raw = payload.get('scheduled_for')
    if not isinstance(scheduled_raw, str) or not scheduled_raw:
        raise InvalidBackgroundMessage('scheduled_for is required')
    scheduled_for = parse_datetime(scheduled_raw)
    if scheduled_for is None or scheduled_for.tzinfo is None:
        raise InvalidBackgroundMessage('scheduled_for must be a timezone-aware timestamp')
    extra = payload.get('payload', {})
    if not isinstance(extra, dict):
        raise InvalidBackgroundMessage('payload must be an object')
    run_id = payload.get('run_id') or ''
    dedupe_key = payload.get('dedupe_key') or ''
    if not isinstance(run_id, str) or not isinstance(dedupe_key, str):
        raise InvalidBackgroundMessage('run_id and dedupe_key must be strings')
    return {
        'version': MESSAGE_VERSION,
        'type': message_type,
        'run_id': run_id,
        'scheduled_for': scheduled_for,
        'dedupe_key': dedupe_key,
        'payload': extra,
    }


def is_stale(scheduled_for, *, now):
    return now - scheduled_for > STALE_AFTER
