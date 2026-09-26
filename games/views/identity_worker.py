"""Private EB Worker endpoint for one named anonymous-merge job.

anonymous.merge advances one named job and never scans the queue.
anonymous.merge_reconcile only publishes due jobs that have no SQS mark.
It does not claim those rows. A merge message without a job id is rejected.
"""

import logging
import time

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from games.anonymous_merge import publish_unmarked_due_merge_jobs, run_named_merge_job
from games.background.messages import (
    ANONYMOUS_MERGE,
    ANONYMOUS_MERGE_RECONCILE,
    InvalidBackgroundMessage,
    parse_background_message,
)
from games.queue_heartbeat import finish as finish_queue_heartbeat
from games.queue_heartbeat import start as start_queue_heartbeat
from games.runtime import RUNTIME_ROLE_IDENTITY, runtime_role
from games.worker_http import is_sqsd_delivery, sqsd_message_id

logger = logging.getLogger('application')


@csrf_exempt
@require_POST
def identity_worker(request):
    if runtime_role() != RUNTIME_ROLE_IDENTITY:
        return HttpResponse('identity worker is disabled for this runtime role', status=503)
    if not is_sqsd_delivery(request, role=RUNTIME_ROLE_IDENTITY):
        return HttpResponse('invalid worker signature', status=403)
    try:
        message = parse_background_message(request.body)
    except InvalidBackgroundMessage:
        return JsonResponse({'error': 'invalid payload'}, status=400)
    if message['type'] == ANONYMOUS_MERGE_RECONCILE:
        message_id = sqsd_message_id(request)
        started = time.perf_counter()
        heartbeat_started = start_queue_heartbeat(
            'anonymous_merge',
            worker='identity:{}'.format(message_id or 'unknown'),
        )
        try:
            published = publish_unmarked_due_merge_jobs(limit=5)
        except Exception as exc:
            finish_queue_heartbeat(
                'anonymous_merge',
                heartbeat_started,
                success=False,
                error='{}: {}'.format(exc.__class__.__name__, exc),
            )
            logger.exception(
                'identity worker reconcile failed run_id=%s message_id=%s',
                message['run_id'], message_id,
            )
            return JsonResponse({'status': 'failed'}, status=500)
        finish_queue_heartbeat(
            'anonymous_merge',
            heartbeat_started,
            success=True,
            processed_count=published,
        )
        logger.info(
            'identity worker reconcile run_id=%s message_id=%s published=%s duration_ms=%.1f',
            message['run_id'], message_id, published,
            (time.perf_counter() - started) * 1000,
        )
        return JsonResponse({'status': 'ok', 'published': published}, status=200)
    if message['type'] != ANONYMOUS_MERGE:
        return JsonResponse({'error': 'unsupported type'}, status=400)
    job_id = message['payload'].get('job_id')
    if not isinstance(job_id, str) or not job_id:
        return JsonResponse({'error': 'job_id is required'}, status=400)

    message_id = sqsd_message_id(request)
    started = time.perf_counter()
    try:
        result = run_named_merge_job(job_id, worker='identity:{}'.format(message_id or 'unknown'))
    except ValueError:
        return JsonResponse({'error': 'invalid job_id'}, status=400)
    except Exception:
        logger.exception(
            'identity worker failed type=%s run_id=%s job_id=%s message_id=%s',
            message['type'], message['run_id'], job_id, message_id,
        )
        return JsonResponse({'status': 'failed'}, status=500)

    logger.info(
        'identity worker %s type=%s run_id=%s job_id=%s message_id=%s duration_ms=%.1f',
        result['status'], message['type'], message['run_id'], job_id, message_id,
        (time.perf_counter() - started) * 1000,
    )
    return JsonResponse({'status': result['status']}, status=200)
