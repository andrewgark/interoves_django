"""Private EB Worker endpoint for integration jobs.

Telegram types send for real. ``instagram.token_refresh`` runs the daily
token check. ``social.publish`` publishes the social queue and does not
run inside the announcement tick.
"""

import logging
import time
from datetime import timedelta

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from games.background.messages import (
    ADMIN_REPORT_STALE_AFTER,
    ANNOUNCEMENT_STALE_AFTER,
    INSTAGRAM_STALE_AFTER,
    INSTAGRAM_TOKEN_REFRESH,
    SOCIAL_PUBLISH,
    SOCIAL_STALE_AFTER,
    TELEGRAM_ADMIN_REPORT,
    TELEGRAM_ANNOUNCEMENTS,
    InvalidBackgroundMessage,
    parse_background_message,
)
from games.instagram.refresh import run_instagram_token_refresh
from games.social.publish import run_social_publish_live
from games.runtime import RUNTIME_ROLE_INTEGRATION, runtime_role
from games.telegram.shadow import run_admin_report_live, run_announcement_live
from games.worker_http import is_sqsd_delivery, sqsd_message_id

logger = logging.getLogger('application')


@csrf_exempt
@require_POST
def integrations_worker(request):
    if runtime_role() != RUNTIME_ROLE_INTEGRATION:
        return HttpResponse('integration worker is disabled for this runtime role', status=503)
    if not is_sqsd_delivery(request, role=RUNTIME_ROLE_INTEGRATION):
        return HttpResponse('invalid worker signature', status=403)
    try:
        message = parse_background_message(request.body)
    except InvalidBackgroundMessage:
        return JsonResponse({'error': 'invalid payload'}, status=400)
    if message['type'] not in (
        TELEGRAM_ANNOUNCEMENTS,
        TELEGRAM_ADMIN_REPORT,
        INSTAGRAM_TOKEN_REFRESH,
        SOCIAL_PUBLISH,
    ):
        return JsonResponse({'error': 'unsupported type'}, status=400)

    message_id = sqsd_message_id(request)
    now = timezone.now()
    if _is_stale(message, now=now):
        logger.info(
            'integration worker skipped_stale type=%s run_id=%s scheduled_for=%s message_id=%s',
            message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
        )
        return JsonResponse({'status': 'skipped_stale'}, status=200)

    started = time.perf_counter()
    try:
        if message['type'] == TELEGRAM_ANNOUNCEMENTS:
            result = run_announcement_live(now=message['scheduled_for'])
        elif message['type'] == TELEGRAM_ADMIN_REPORT:
            result = run_admin_report_live(now=message['scheduled_for'])
        elif message['type'] == INSTAGRAM_TOKEN_REFRESH:
            result = run_instagram_token_refresh(
                worker='integration:{}'.format(message_id or 'unknown'),
            )
        else:
            result = run_social_publish_live(now=message['scheduled_for'])
        if result is None:
            logger.info(
                'integration worker skipped_locked type=%s run_id=%s scheduled_for=%s message_id=%s',
                message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
            )
            return JsonResponse({'status': 'skipped_locked'}, status=200)
        outcome = 'ok'
    except Exception:
        logger.exception(
            'integration worker failed type=%s run_id=%s scheduled_for=%s message_id=%s',
            message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
        )
        return JsonResponse({'status': 'failed'}, status=500)

    logger.info(
        'integration worker %s type=%s run_id=%s scheduled_for=%s message_id=%s duration_ms=%.1f',
        outcome, message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
        (time.perf_counter() - started) * 1000,
    )
    return JsonResponse({'status': outcome, 'result': _jsonable(result)}, status=200)


def _is_stale(message, *, now):
    age = now - message['scheduled_for']
    if message['type'] == TELEGRAM_ADMIN_REPORT:
        return age > ADMIN_REPORT_STALE_AFTER
    if message['type'] == TELEGRAM_ANNOUNCEMENTS:
        return age > ANNOUNCEMENT_STALE_AFTER
    if message['type'] == INSTAGRAM_TOKEN_REFRESH:
        return age > INSTAGRAM_STALE_AFTER
    if message['type'] == SOCIAL_PUBLISH:
        return age > SOCIAL_STALE_AFTER
    return age > timedelta(minutes=2)


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
