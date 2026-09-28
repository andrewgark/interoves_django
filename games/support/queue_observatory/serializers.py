"""JSON-safe representations for support queue observability."""

from django.urls import reverse
from django.utils import timezone

from .explanations import explain_item_state, explain_job_state, explain_outbox_state


def _iso(value):
    return value.isoformat() if value else None


def _age_seconds(value, *, now=None):
    if value is None:
        return None
    now = now or timezone.now()
    return max(0, int((now - value).total_seconds()))


def serialize_outbox(outbox, *, now=None):
    return {
        'id': outbox.pk,
        'item_id': outbox.item_id,
        'task_revision': str(outbox.task_revision),
        'status': outbox.status,
        'attempts': outbox.attempts,
        'created_at': _iso(outbox.created_at),
        'updated_at': _iso(outbox.updated_at),
        'claimed_until': _iso(outbox.claimed_until),
        'sent_at': _iso(outbox.sent_at),
        'next_attempt_at': _iso(outbox.next_attempt_at),
        'last_error': (outbox.last_error or '')[:2000],
        'explanation': explain_outbox_state(outbox, now=now),
    }


def _actor_payload(item):
    team = item.team
    user = item.user
    return {
        'team_id': item.team_id,
        'team_name': (getattr(team, 'visible_name', '') or getattr(team, 'name', '')) if team else None,
        'user_id': item.user_id,
        'username': user.username if user else None,
        'anon_key': item.anon_key,
        'replay_slot_id': item.replay_slot_id,
        'actor_key': item.actor_key,
    }


def serialize_item(item, *, outboxes=None, now=None):
    now = now or timezone.now()
    outboxes = list(outboxes if outboxes is not None else item.outbox_rows.all())
    current_outbox = next(
        (row for row in reversed(outboxes) if row.status != 'cancelled'),
        outboxes[-1] if outboxes else None,
    )
    return {
        'id': item.pk,
        'job_id': item.job_id,
        'actor': _actor_payload(item),
        'status': item.status,
        'attempt_count': item.attempt_count,
        'credited_attempts': item.credited_attempts,
        'created_at': _iso(item.created_at),
        'updated_at': _iso(item.updated_at),
        'started_at': _iso(item.started_at),
        'completed_at': _iso(item.completed_at),
        'age_seconds': _age_seconds(item.created_at, now=now),
        'processing_seconds': (
            max(0, int((item.completed_at - item.started_at).total_seconds()))
            if item.started_at and item.completed_at else None
        ),
        'next_attempt_at': _iso(item.next_attempt_at),
        'claimed_until': _iso(item.claimed_until),
        'last_error': (item.last_error or '')[:2000],
        'outbox_id': current_outbox.pk if current_outbox else None,
        'explanation': explain_item_state(item, outbox=current_outbox, now=now),
        'outbox': [serialize_outbox(row, now=now) for row in outboxes],
    }


def _counts(items):
    statuses = ('pending', 'running', 'completed', 'failed', 'superseded')
    return {status: sum(item.status == status for item in items) for status in statuses}


def serialize_job_summary(job, *, counts=None, now=None):
    now = now or timezone.now()
    counts = counts or {}
    return {
        'id': job.pk,
        'url': reverse('support:queue_observatory_job_detail', kwargs={'job_id': job.pk}),
        'observatory_url': reverse('support:queue_observatory_job_page', kwargs={'job_id': job.pk}),
        'task_id': job.task_id,
        'game_id': job.game_id,
        'task_revision': str(job.task_revision),
        'status': job.status,
        'replay_mode': (job.pending_resolution or {}).get('replay_mode', 'full_recheck'),
        'total_items': job.total_actors,
        'completed_items': job.completed_actors,
        'progress': job.progress,
        'counts': counts,
        'attempt_count': job.attempt_count,
        'created_at': _iso(job.created_at),
        'updated_at': _iso(job.updated_at),
        'started_at': _iso(job.started_at),
        'completed_at': _iso(job.completed_at),
        'age_seconds': _age_seconds(job.created_at, now=now),
        'next_attempt_at': _iso(job.next_attempt_at),
        'claimed_until': _iso(job.claimed_until),
        'last_error': (job.last_error or '')[:2000],
        'explanation': explain_job_state(job, now=now),
    }


def serialize_job_detail(job, *, items, now=None):
    now = now or timezone.now()
    items = list(items)
    outboxes = [row for item in items for row in item.outbox_rows.all()]
    counts = _counts(items)
    payload = serialize_job_summary(job, counts=counts, now=now)
    payload.update({
        'pending_resolution': job.pending_resolution or {},
        'explanation': explain_job_state(
            job, open_items=[item for item in items if item.status in ('pending', 'running')],
            outboxes=outboxes, now=now,
        ),
        'items': [
            serialize_item(item, outboxes=list(item.outbox_rows.all()), now=now)
            for item in items
        ],
        'outbox': [serialize_outbox(row, now=now) for row in outboxes],
    })
    return payload
