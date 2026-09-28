"""Read-only JSON endpoints for queue observability."""

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from games.support.access import support_console_required

from .selectors import job_detail_queryset, job_list_queryset, queue_summary
from .serializers import serialize_job_detail, serialize_job_summary


def _positive_int(value, *, default=None, maximum=None):
    if value in (None, ''):
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError('Expected integer')
    if value < 1 or maximum is not None and value > maximum:
        raise ValueError('Integer is out of range')
    return value


def _parse_updated_since(value):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError('Invalid updated_since')
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


@support_console_required
@require_GET
def summary(request):
    payload = queue_summary()
    for section in ('oldest_pending_job_at', 'oldest_pending_item_at', 'oldest_pending_outbox_at', 'observed_at'):
        value = payload[section]
        payload[section] = value.isoformat() if value else None
    return JsonResponse({'ok': True, 'summary': payload})


@support_console_required
@require_GET
def jobs(request):
    try:
        page = _positive_int(request.GET.get('page'), default=1)
        page_size = _positive_int(request.GET.get('page_size'), default=50, maximum=100)
        task_id = _positive_int(request.GET.get('task_id'))
        game_id = request.GET.get('game_id') or None
        updated_since = _parse_updated_since(request.GET.get('updated_since'))
        queryset = job_list_queryset(
            status=request.GET.get('status') or None,
            task_id=task_id,
            game_id=game_id,
            actor=request.GET.get('actor') or None,
            updated_since=updated_since,
        )
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    offset = (page - 1) * page_size
    rows = list(queryset[offset:offset + page_size])
    payload = []
    for job in rows:
        counts = {
            'pending': job.pending_count,
            'running': job.running_count,
            'completed': job.completed_count,
            'failed': job.failed_count,
            'superseded': job.superseded_count,
        }
        payload.append(serialize_job_summary(job, counts=counts))
    return JsonResponse({
        'ok': True,
        'jobs': payload,
        'page': page,
        'page_size': page_size,
        'has_next': queryset[offset + page_size:offset + page_size + 1].exists(),
    })


@support_console_required
@require_GET
def job_detail(request, job_id):
    job = job_detail_queryset().filter(pk=job_id).first()
    if job is None:
        return JsonResponse({'ok': False, 'error': 'Job not found'}, status=404)
    return JsonResponse({'ok': True, 'job': serialize_job_detail(job, items=job.items.all())})
