"""Durable, nonblocking handoff from gameplay writes to projection workers."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from games.models import (
    DailyResultProjectionRefreshOutbox,
    GameTaskGroup,
)

logger = logging.getLogger('application')

CLAIM_TIMEOUT = timedelta(minutes=2)
RETRY_BASE_SECONDS = 2
RETRY_MAX_SECONDS = 5 * 60
OUTBOX_RETENTION = timedelta(days=30)


def enqueue_actor_projection_refresh(
    game, task_group, *, team=None, user=None, anon_key=None,
    team_id=None, user_id=None,
):
    """Persist one actor invalidation in the source mutation transaction.

    This intentionally does not touch ``DailyResultProjectionState``. That row
    is also written by projection workers and can be locked during a large
    refresh; gameplay must not wait for that lock.
    """
    if game is None or task_group is None or getattr(game, 'project_id', None) != 'sections':
        return None
    if team is not None or team_id is not None:
        actor_type = 'team'
        actor_key = str(team.pk if team is not None else team_id)
    elif user is not None or user_id is not None:
        actor_type = 'user'
        actor_key = str(getattr(user, 'pk', user) if user is not None else user_id)
    elif anon_key:
        actor_type = 'anon'
        actor_key = str(anon_key)
    else:
        return None
    if not GameTaskGroup.objects.filter(game=game, task_group=task_group).exists():
        return None
    return DailyResultProjectionRefreshOutbox.objects.create(
        game=game,
        task_group=task_group,
        actor_type=actor_type,
        actor_key=actor_key,
    )


def _claim_many(*, limit, now=None):
    now = now or timezone.now()
    limit = max(0, int(limit))
    if not limit:
        return []
    eligible = (
        Q(status=DailyResultProjectionRefreshOutbox.STATUS_PENDING)
        & (Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
    ) | (
        Q(status=DailyResultProjectionRefreshOutbox.STATUS_SENDING)
        & (Q(claimed_until__isnull=True) | Q(claimed_until__lte=now))
    )
    with transaction.atomic():
        rows = list(
            DailyResultProjectionRefreshOutbox.objects.filter(eligible)
            .select_for_update()
            .select_related('game', 'task_group')
            .order_by('id')[:limit]
        )
        for row in rows:
            row.status = DailyResultProjectionRefreshOutbox.STATUS_SENDING
            row.claim_token = uuid.uuid4()
            row.claimed_until = now + CLAIM_TIMEOUT
            row.attempts += 1
            row.updated_at = now
        if rows:
            DailyResultProjectionRefreshOutbox.objects.bulk_update(
                rows, ['status', 'claim_token', 'claimed_until', 'attempts', 'updated_at'],
            )
    return rows


def _prepare_claim(row):
    """Write the durable dirty actor and revision away from the web request."""
    if row.prepared_revision is not None:
        return row.prepared_revision
    from games.daily.projection import mark_projection_dirty

    actor_filter = {
        'team_id': row.actor_key if row.actor_type == 'team' else None,
        'user_id': int(row.actor_key) if row.actor_type == 'user' else None,
        'anon_key': row.actor_key if row.actor_type == 'anon' else None,
    }
    with transaction.atomic():
        locked = DailyResultProjectionRefreshOutbox.objects.select_for_update().get(
            pk=row.pk, status=DailyResultProjectionRefreshOutbox.STATUS_SENDING,
            claim_token=row.claim_token,
        )
        if locked.prepared_revision is not None:
            return locked.prepared_revision
        revision = mark_projection_dirty(
            row.game, row.task_group, actor=True, actor_filter=actor_filter,
        )
        locked.prepared_revision = revision
        locked.updated_at = timezone.now()
        locked.save(update_fields=['prepared_revision', 'updated_at'])
    return revision


def _mark_sent(row):
    now = timezone.now()
    return DailyResultProjectionRefreshOutbox.objects.filter(
        pk=row.pk,
        status=DailyResultProjectionRefreshOutbox.STATUS_SENDING,
        claim_token=row.claim_token,
    ).update(
        status=DailyResultProjectionRefreshOutbox.STATUS_SENT,
        sent_at=now,
        claimed_until=None,
        claim_token=None,
        next_attempt_at=None,
        last_error='',
        updated_at=now,
    )


def _mark_failed(row, exc):
    now = timezone.now()
    delay = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** min(row.attempts - 1, 16)))
    return DailyResultProjectionRefreshOutbox.objects.filter(
        pk=row.pk,
        status=DailyResultProjectionRefreshOutbox.STATUS_SENDING,
        claim_token=row.claim_token,
    ).update(
        status=DailyResultProjectionRefreshOutbox.STATUS_PENDING,
        claimed_until=None,
        claim_token=None,
        next_attempt_at=now + timedelta(seconds=delay),
        last_error='{}: {}'.format(exc.__class__.__name__, exc)[:2000],
        updated_at=now,
    )


def dispatch_actor_projection_outbox(*, limit=10, now=None):
    """Mark and publish pending actor refreshes; safe to retry after crashes."""
    now = now or timezone.now()
    rows = _claim_many(limit=limit, now=now)
    sent = failed = 0
    from games.projection_events import events_enabled, publish_projection_refresh

    for row in rows:
        try:
            revision = _prepare_claim(row)
            if revision is None:
                sent += bool(_mark_sent(row))
                continue
            if events_enabled():
                published = publish_projection_refresh(
                    row.game_id, row.task_group_id, mode='actor',
                )
                if not published:
                    raise RuntimeError('projection refresh event was not published')
            else:
                from games.daily.projection import _refresh_dirty_actors
                _refresh_dirty_actors(row.game_id, row.task_group_id)
            sent += bool(_mark_sent(row))
        except Exception as exc:
            failed += bool(_mark_failed(row, exc))
            logger.exception(
                'daily_result_projection_outbox failed outbox_id=%s game=%s task_group=%s',
                row.pk, row.game_id, row.task_group_id,
            )
    stale_ids = DailyResultProjectionRefreshOutbox.objects.filter(
        status=DailyResultProjectionRefreshOutbox.STATUS_SENT,
        sent_at__lt=now - OUTBOX_RETENTION,
    ).order_by('id').values_list('id', flat=True)[:500]
    DailyResultProjectionRefreshOutbox.objects.filter(pk__in=stale_ids).delete()
    return {'claimed': len(rows), 'sent': sent, 'failed': failed}
