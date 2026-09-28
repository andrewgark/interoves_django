"""Bounded database selectors for the queue observatory."""

from django.conf import settings
from django.db.models import Count, Min, Prefetch, Q
from django.utils import timezone

from games.models import WordSaladRecheckItem, WordSaladRecheckJob, WordSaladRecheckOutbox


JOB_STATUSES = {value for value, _ in WordSaladRecheckJob.STATUS_CHOICES}


def _job_queryset():
    return WordSaladRecheckJob.objects.select_related('task', 'game')


def job_list_queryset(*, status=None, task_id=None, game_id=None, actor=None, updated_since=None):
    queryset = _job_queryset().annotate(
        pending_count=Count('items', filter=Q(items__status=WordSaladRecheckItem.STATUS_PENDING)),
        running_count=Count('items', filter=Q(items__status=WordSaladRecheckItem.STATUS_RUNNING)),
        completed_count=Count('items', filter=Q(items__status=WordSaladRecheckItem.STATUS_COMPLETED)),
        failed_count=Count('items', filter=Q(items__status=WordSaladRecheckItem.STATUS_FAILED)),
        superseded_count=Count('items', filter=Q(items__status=WordSaladRecheckItem.STATUS_SUPERSEDED)),
    )
    if status:
        if status not in JOB_STATUSES:
            raise ValueError('Unknown job status')
        queryset = queryset.filter(status=status)
    if task_id is not None:
        queryset = queryset.filter(task_id=task_id)
    if game_id is not None:
        queryset = queryset.filter(game_id=game_id)
    if actor:
        if str(actor).isdigit():
            queryset = queryset.filter(Q(items__team_id=int(actor)) | Q(items__user_id=int(actor)))
        else:
            queryset = queryset.filter(items__anon_key=actor)
    if updated_since is not None:
        queryset = queryset.filter(updated_at__gt=updated_since)
    return queryset.distinct().order_by('-updated_at', '-id')


def job_detail_queryset():
    outbox_queryset = WordSaladRecheckOutbox.objects.order_by('id')
    item_queryset = (
        WordSaladRecheckItem.objects
        .select_related('team', 'user', 'replay_slot')
        .prefetch_related(Prefetch('outbox_rows', queryset=outbox_queryset))
        .order_by('id')
    )
    return _job_queryset().prefetch_related(Prefetch('items', queryset=item_queryset))


def queue_summary(*, now=None):
    now = now or timezone.now()
    jobs = WordSaladRecheckJob.objects
    items = WordSaladRecheckItem.objects
    outboxes = WordSaladRecheckOutbox.objects
    lease_cutoff = now
    item_retry_q = Q(attempt_count__gt=0, status=WordSaladRecheckItem.STATUS_PENDING)
    summary = {
        'queue': 'word_salad_recheck',
        'jobs': {
            status: jobs.filter(status=status).count()
            for status, _ in WordSaladRecheckJob.STATUS_CHOICES
        },
        'items': {
            status: items.filter(status=status).count()
            for status, _ in WordSaladRecheckItem.STATUS_CHOICES
        },
        'outbox': {
            status: outboxes.filter(status=status).count()
            for status, _ in WordSaladRecheckOutbox.STATUS_CHOICES
        },
        'retrying_items': items.filter(item_retry_q).count(),
        'overdue_retries': items.filter(
            Q(status=WordSaladRecheckItem.STATUS_PENDING),
            next_attempt_at__lt=now,
        ).count(),
        'stale_jobs': jobs.filter(
            status=WordSaladRecheckJob.STATUS_RUNNING,
            claimed_until__isnull=False,
            claimed_until__lte=lease_cutoff,
        ).count(),
        'stale_items': items.filter(
            status=WordSaladRecheckItem.STATUS_RUNNING,
            claimed_until__isnull=False,
            claimed_until__lte=lease_cutoff,
        ).count(),
        'stale_outbox': outboxes.filter(
            status=WordSaladRecheckOutbox.STATUS_SENDING,
            claimed_until__isnull=False,
            claimed_until__lte=now,
        ).count(),
        'oldest_pending_job_at': jobs.filter(status=WordSaladRecheckJob.STATUS_PENDING)
            .aggregate(value=Min('created_at'))['value'],
        'oldest_pending_item_at': items.filter(status=WordSaladRecheckItem.STATUS_PENDING)
            .aggregate(value=Min('created_at'))['value'],
        'oldest_pending_outbox_at': outboxes.filter(status=WordSaladRecheckOutbox.STATUS_PENDING)
            .aggregate(value=Min('created_at'))['value'],
        'observed_at': now,
        'leases_seconds': {
            'job': getattr(settings, 'RECHECK_JOB_LEASE_SECONDS', 600),
            'item': getattr(settings, 'RECHECK_ITEM_LEASE_SECONDS', 600),
            'outbox_claim': 300,
        },
    }
    for field in ('job', 'item', 'outbox'):
        value = summary['oldest_pending_{}_at'.format(field)]
        summary['oldest_pending_{}_age_seconds'.format(field)] = (
            max(0, int((now - value).total_seconds())) if value else None
        )
    return summary
