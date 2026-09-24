"""Private EB Worker delivery endpoint for one Word Salad item."""

import hashlib
import hmac
import json
import logging
import os
import time

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from games.models import WordSaladRecheckItem
from games.runtime import runtime_role
from games.word_salad_recheck import process_word_salad_recheck_item

logger = logging.getLogger('application')
MAX_SIGNATURE_AGE = 300
VALIDATION_HTTP_500_SENTINEL = '/tmp/interoves-validation-http-500-once'


def _validation_http_500_once():
    if (
        runtime_role() != 'worker'
        or os.environ.get('INTEROVES_VALIDATION_MODE') != '1'
        or os.environ.get('INTEROVES_VALIDATION_HTTP_500_ONCE') != '1'
    ):
        return False
    try:
        fd = os.open(VALIDATION_HTTP_500_SENTINEL, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    else:
        os.close(fd)
        return True


def _valid_signature(request, body):
    secret = os.environ.get('WORD_SALAD_WORKER_HMAC_SECRET', '').encode()
    timestamp = request.headers.get('X-Interoves-Worker-Timestamp', '')
    signature = request.headers.get('X-Interoves-Worker-Signature', '')
    if not secret or not timestamp or not signature:
        return False
    try:
        age = abs(time.time() - int(timestamp))
    except (TypeError, ValueError):
        return False
    if age > MAX_SIGNATURE_AGE:
        return False
    signed = timestamp.encode() + b'.' + body
    expected = hmac.new(secret, signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix('sha256='), expected)


def _authorized_delivery(request, body):
    """Accept signed calls or the private, native EB sqsd delivery.

    sqsd does not provide a configurable HMAC header.  Its native delivery is
    therefore protected by the private worker environment/security group and
    by restricting this fallback to the sqsd user-agent plus message id.  A
    signed reverse proxy can use the stronger HMAC path whenever one exists.
    """
    if _valid_signature(request, body):
        return True
    if os.environ.get('INTEROVES_RUNTIME_ROLE', '').strip().lower() != 'worker':
        return False
    user_agent = request.headers.get('User-Agent', '')
    return bool(
        request.headers.get('X-Aws-Sqsd-Msgid', '').strip()
        and user_agent.lower().startswith('aws-sqsd')
    )


@csrf_exempt
@require_POST
def word_salad_worker(request):
    if runtime_role() == 'web':
        return HttpResponse('worker endpoint is disabled for web runtime role', status=503)
    body = request.body
    if not _authorized_delivery(request, body):
        return HttpResponse('invalid worker signature', status=403)
    try:
        payload = json.loads(body.decode('utf-8'))
        job_id = int(payload['job_id'])
        item_id = int(payload['item_id'])
        actor_id = str(payload['actor_id'])
        task_revision = str(payload['task_revision'])
    except (UnicodeDecodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return JsonResponse({'error': 'invalid payload'}, status=400)
    if payload.get('version') != 1 or payload.get('operation') != 'word_salad_recheck':
        return JsonResponse({'error': 'unsupported operation'}, status=400)

    item = WordSaladRecheckItem.objects.select_related('job').filter(pk=item_id, job_id=job_id).first()
    if item is None:
        # A deleted item cannot be made current by a stale delivery. ACK it.
        return JsonResponse({'status': 'unknown_item'}, status=200)
    if actor_id != item.actor_key:
        return JsonResponse({'error': 'actor/item mismatch'}, status=400)
    if task_revision != str(item.job.task_revision):
        return JsonResponse({'status': 'stale_revision'}, status=200)

    if _validation_http_500_once():
        logger.warning('validation-only worker HTTP 500 failpoint triggered item_id=%s', item_id)
        return JsonResponse({'error': 'validation transient failure'}, status=500)

    result = process_word_salad_recheck_item(
        job_id=job_id,
        item_id=item_id,
        worker='eb-worker:{}'.format(request.headers.get('X-Aws-Sqsd-Msgid', 'unknown')),
    )
    logger.info(
        'word salad worker delivery result=%s job_id=%s item_id=%s task_revision=%s',
        result, job_id, item_id, task_revision,
    )
    if result in ('completed', 'superseded', 'not_due'):
        return JsonResponse({'status': result}, status=200)
    if result == 'lease_conflict':
        return JsonResponse({'status': result}, status=409)
    return JsonResponse({'status': result}, status=500)
