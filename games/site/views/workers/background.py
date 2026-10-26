"""Private EB Worker endpoint for scheduled background jobs.

Accepts ``difficulty.refresh``, ``difficulty.minute.refresh`` (and its
historical name ``difficulty.censorly.refresh``), ``difficulty.health_check``,
``projection.reconcile``, and ``projection.refresh``. The reconcile scan
is the safety net. ``projection.refresh`` rebuilds one dirty release and
is not stale-skipped. Other runtime roles return 503.
"""

import logging
import time

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from games.ops.workers.messages import (
    DIFFICULTY_HEALTH_CHECK,
    DIFFICULTY_CENSORLY_REFRESH,
    DIFFICULTY_MINUTE_REFRESH,
    DIFFICULTY_REFRESH,
    DAILY_STATISTICS_REFRESH,
    PROJECTION_RECONCILE,
    PROJECTION_REFRESH,
    InvalidBackgroundMessage,
    is_stale,
    parse_background_message,
)
from games.daily.projection.cron import (
    PROJECTION_CRON_LOCK_NAME,
    PROJECTION_REPAIR_LIMIT,
    reconcile_projection_releases,
)
from games.ops.workers.heartbeat import finish as finish_queue_heartbeat
from games.ops.workers.heartbeat import start as start_queue_heartbeat
from games.ops.cron.lock import distributed_cron_lock
from games.daily.difficulty.core import DUE_REFRESH_LIMIT
from games.daily.difficulty.health import run_daily_difficulty_health_check
from games.daily.difficulty.refresh import (
    DIFFICULTY_REFRESH_LOCK,
    DIFFICULTY_REFRESH_LOCK_TTL_SECONDS,
    run_daily_difficulty_refresh,
    run_minute_difficulty_refresh,
)
from games.realtime.projection import (
    run_named_daily_statistics_refresh,
    run_named_projection_refresh,
)
from games.runtime import RUNTIME_ROLE_BACKGROUND, runtime_role
from games.club.billing.gifts.payments import reconcile_yookassa_gift_payments
from games.club.billing.gifts.offers import expire_subscription_gifts
from games.ops.workers.http import is_sqsd_delivery, sqsd_message_id

logger = logging.getLogger('application')


@csrf_exempt
@require_POST
def background_worker(request):
    if runtime_role() != RUNTIME_ROLE_BACKGROUND:
        return HttpResponse('background worker is disabled for this runtime role', status=503)
    if not is_sqsd_delivery(request, role=RUNTIME_ROLE_BACKGROUND):
        return HttpResponse('invalid worker signature', status=403)
    try:
        message = parse_background_message(request.body)
    except InvalidBackgroundMessage:
        return JsonResponse({'error': 'invalid payload'}, status=400)
    if message['type'] not in (
        DIFFICULTY_REFRESH, DIFFICULTY_CENSORLY_REFRESH, DIFFICULTY_MINUTE_REFRESH,
        DIFFICULTY_HEALTH_CHECK,
        PROJECTION_RECONCILE, PROJECTION_REFRESH, DAILY_STATISTICS_REFRESH,
    ):
        return JsonResponse({'error': 'unsupported type'}, status=400)

    message_id = sqsd_message_id(request)
    now = timezone.now()
    if message['type'] == PROJECTION_REFRESH:
        return _projection_refresh(message, message_id)
    if message['type'] == DAILY_STATISTICS_REFRESH:
        return _daily_statistics_refresh(message, message_id)
    if is_stale(message['scheduled_for'], now=now):
        logger.info(
            'background worker skipped_stale type=%s run_id=%s scheduled_for=%s message_id=%s',
            message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
        )
        return JsonResponse({'status': 'skipped_stale'}, status=200)

    if message['type'] == DIFFICULTY_HEALTH_CHECK:
        return _health_check(message, message_id)
    if message['type'] == PROJECTION_RECONCILE:
        return _projection_reconcile(message, message_id)

    if message['type'] in (DIFFICULTY_MINUTE_REFRESH, DIFFICULTY_CENSORLY_REFRESH):
        # ``difficulty.censorly.refresh`` is the historical name of the minute
        # tick; it now covers every daily game, like ``difficulty.minute.refresh``.
        started = time.perf_counter()
        try:
            with distributed_cron_lock(
                DIFFICULTY_REFRESH_LOCK,
                ttl_seconds=DIFFICULTY_REFRESH_LOCK_TTL_SECONDS,
            ) as acquired:
                if not acquired:
                    return JsonResponse({'status': 'skipped_locked'}, status=200)
                results = run_minute_difficulty_refresh(
                    worker='background:minute:{}'.format(message_id or 'unknown'),
                )
        except Exception:
            logger.exception(
                'background worker failed type=%s run_id=%s message_id=%s',
                message['type'], message['run_id'], message_id,
            )
            return JsonResponse({'status': 'failed'}, status=500)
        logger.info(
            'background worker ok type=%s run_id=%s message_id=%s refreshed=%s duration_ms=%.1f',
            message['type'], message['run_id'], message_id,
            len(results), (time.perf_counter() - started) * 1000,
        )
        return JsonResponse({'status': 'ok', 'refreshed': len(results)}, status=200)

    started = time.perf_counter()
    try:
        with distributed_cron_lock(
            DIFFICULTY_REFRESH_LOCK,
            ttl_seconds=DIFFICULTY_REFRESH_LOCK_TTL_SECONDS,
        ) as acquired:
            if not acquired:
                logger.info(
                    'background worker skipped_locked type=%s run_id=%s scheduled_for=%s message_id=%s',
                    message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
                )
                return JsonResponse({'status': 'skipped_locked'}, status=200)
            results = run_daily_difficulty_refresh(
                limit=DUE_REFRESH_LIMIT,
                worker='background:{}'.format(message_id or 'unknown'),
            )
    except Exception:
        logger.exception(
            'background worker failed type=%s run_id=%s scheduled_for=%s message_id=%s',
            message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
        )
        return JsonResponse({'status': 'failed'}, status=500)

    expired_gifts = expire_subscription_gifts()
    reconciled_gifts = 0
    with distributed_cron_lock('subscription_gifts_yookassa_reconcile', ttl_seconds=300) as acquired:
        if acquired:
            reconciled_gifts = reconcile_yookassa_gift_payments(limit=50)

    logger.info(
        'background worker ok type=%s run_id=%s scheduled_for=%s message_id=%s refreshed=%s '
        'expired_gifts=%s reconciled_gifts=%s duration_ms=%.1f',
        message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
        len(results), expired_gifts, reconciled_gifts, (time.perf_counter() - started) * 1000,
    )
    response = {'status': 'ok', 'refreshed': len(results)}
    if expired_gifts:
        response['expired_gifts'] = expired_gifts
    if reconciled_gifts:
        response['reconciled_gifts'] = reconciled_gifts
    return JsonResponse(response, status=200)


def _projection_reconcile(message, message_id):
    started = time.perf_counter()
    heartbeat_started = start_queue_heartbeat(
        PROJECTION_CRON_LOCK_NAME,
        worker='background:{}'.format(message_id or 'unknown'),
    )
    try:
        result = reconcile_projection_releases(apply=True, limit=PROJECTION_REPAIR_LIMIT)
        if not result['skipped_locked']:
            from games.results.snapshots.aggregate_snapshots import reconcile_monthly_snapshots
            result['aggregate_snapshots'] = reconcile_monthly_snapshots(limit=2)
    except Exception as exc:
        finish_queue_heartbeat(
            PROJECTION_CRON_LOCK_NAME,
            heartbeat_started,
            success=False,
            error='{}: {}'.format(exc.__class__.__name__, exc),
        )
        logger.exception(
            'background worker failed type=%s run_id=%s scheduled_for=%s message_id=%s',
            message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
        )
        return JsonResponse({'status': 'failed'}, status=500)
    finish_queue_heartbeat(
        PROJECTION_CRON_LOCK_NAME,
        heartbeat_started,
        success=True,
        processed_count=result.get('rebuilt'),
    )
    if result['skipped_locked']:
        logger.info(
            'background worker skipped_locked type=%s run_id=%s scheduled_for=%s message_id=%s',
            message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
        )
        return JsonResponse({'status': 'skipped_locked'}, status=200)
    logger.info(
        'background worker ok type=%s run_id=%s scheduled_for=%s message_id=%s '
        'mode=%s scanned=%s valid=%s missing=%s stale=%s rebuilt=%s duration_ms=%.1f',
        message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
        result['mode'], result['scanned'], result['valid'], result['missing'], result['stale'],
        result['rebuilt'], (time.perf_counter() - started) * 1000,
    )
    return JsonResponse({
        'status': 'ok',
        'mode': result['mode'],
        'scanned': result['scanned'],
        'valid': result['valid'],
        'missing': result['missing'],
        'stale': result['stale'],
        'rebuilt': result['rebuilt'],
        'invalid': result['invalid'],
    }, status=200)


def _projection_refresh(message, message_id):
    payload = message['payload']
    game_id = payload.get('game_id')
    task_group_id = payload.get('task_group_id')
    mode = payload.get('mode')
    if not isinstance(game_id, str) or not game_id or isinstance(task_group_id, bool) or not isinstance(task_group_id, int):
        return JsonResponse({'error': 'invalid payload'}, status=400)
    if mode not in ('actor', 'full'):
        return JsonResponse({'error': 'invalid payload'}, status=400)
    started = time.perf_counter()
    try:
        status = run_named_projection_refresh(
            game_id=game_id, task_group_id=task_group_id, mode=mode,
        )
    except Exception:
        logger.exception(
            'background worker failed type=%s run_id=%s scheduled_for=%s message_id=%s',
            message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
        )
        return JsonResponse({'status': 'failed'}, status=500)
    logger.info(
        'background worker ok type=%s run_id=%s scheduled_for=%s message_id=%s '
        'mode=%s status=%s queue_wait_ms=%.1f duration_ms=%.1f',
        message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
        mode, status,
        max(0.0, (timezone.now() - message['scheduled_for']).total_seconds() * 1000),
        (time.perf_counter() - started) * 1000,
    )
    return JsonResponse({'status': status}, status=500 if status == 'retry' else 200)


def _daily_statistics_refresh(message, message_id):
    payload = message['payload']
    game_id = payload.get('game_id')
    task_group_id = payload.get('task_group_id')
    if (
        not isinstance(game_id, str) or not game_id
        or isinstance(task_group_id, bool) or not isinstance(task_group_id, int)
    ):
        return JsonResponse({'error': 'invalid payload'}, status=400)
    started = time.perf_counter()
    try:
        status = run_named_daily_statistics_refresh(
            game_id=game_id, task_group_id=task_group_id,
        )
    except Exception:
        logger.exception(
            'background worker failed type=%s run_id=%s message_id=%s',
            message['type'], message['run_id'], message_id,
        )
        return JsonResponse({'status': 'failed'}, status=500)
    logger.info(
        'background worker ok type=%s run_id=%s message_id=%s status=%s '
        'queue_wait_ms=%.1f duration_ms=%.1f',
        message['type'], message['run_id'], message_id, status,
        max(0.0, (timezone.now() - message['scheduled_for']).total_seconds() * 1000),
        (time.perf_counter() - started) * 1000,
    )
    if status == 'retry':
        return JsonResponse({'status': status}, status=500)
    return JsonResponse({'status': status}, status=200)


def _health_check(message, message_id):
    started = time.perf_counter()
    try:
        result = run_daily_difficulty_health_check()
    except Exception:
        logger.exception(
            'background worker failed type=%s run_id=%s scheduled_for=%s message_id=%s',
            message['type'], message['run_id'], message['scheduled_for'].isoformat(), message_id,
        )
        return JsonResponse({'status': 'failed'}, status=500)
    status = result['status']
    logger.info(
        'background worker %s type=%s run_id=%s scheduled_for=%s message_id=%s duration_ms=%.1f',
        status, message['type'], message['run_id'], message['scheduled_for'].isoformat(),
        message_id, (time.perf_counter() - started) * 1000,
    )
    if status == 'skipped_locked':
        return JsonResponse({'status': 'skipped_locked'}, status=200)
    if status == 'skipped_recent':
        return JsonResponse({'status': 'skipped_recent'}, status=200)
    if status == 'unhealthy':
        return JsonResponse({'status': 'ok', 'healthy': False}, status=200)
    return JsonResponse({'status': 'ok', 'healthy': True}, status=200)
