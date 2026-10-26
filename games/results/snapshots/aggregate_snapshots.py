"""Durable monthly table publications; HTTP only requests work and reads versions."""

import hashlib
import json
import logging
import uuid
from datetime import timedelta
from dataclasses import replace
from types import SimpleNamespace

from django.db import transaction
from django.db.models import Case, F, Q, Value, When
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from games.models import (
    DailyAggregateSnapshot, DailyAggregateSnapshotRow, DailyAggregateSnapshotVersion,
    DailyResultProjection, DailyResultProjectionRefreshOutbox, DailyResultProjectionState,
    Profile, ProfileTeamMembership, Task, TaskGroup,
)

logger = logging.getLogger('application')
LEASE_SECONDS = 120
RETRY_SECONDS = 30
VERSION_RETENTION = timedelta(days=1)
BUILD_PAGE_SIZE = 250


def _requested_at_if_idle():
    # Preserve queue position while additional changes are coalesced.
    return Case(
        When(requested_revision=F('completed_revision'), then=Value(timezone.now())),
        default=F('requested_at'),
    )


class SnapshotVersionExpired(ValueError):
    """A progressive request must never append rows from a different version."""


def _source_versions(group_ids, states):
    result = {}
    for group_id in group_ids:
        state = states.get(group_id)
        if state is None or not state.coverage_complete:
            result[str(group_id)] = None
            continue
        # Legacy complete rows have no separate publication token yet. Their
        # timestamp is only a change detector, never a claimed successful date.
        result[str(group_id)] = str(state.published_version or (
            'legacy:{}:{}'.format(state.adapter_version, state.completed_at.isoformat())
        ))
    return result


def _states(game, group_ids, *, lock=False):
    qs = DailyResultProjectionState.objects.filter(game=game, task_group_id__in=group_ids)
    if lock:
        qs = qs.select_for_update()
    return {row.task_group_id: row for row in qs}


def _serialize_columns(columns):
    return [{
        'link_id': col.link.pk, 'group_id': col.link.task_group_id,
        'number': str(col.link.number), 'label': str(col.label),
        'max_score': float(col.max_score) if col.max_score is not None else None,
        'url': col.url,
        'published_at': col.published_at.isoformat() if col.published_at else None,
        'task_ids': sorted(task.pk for task in col.link.task_group.result_tasks),
    } for col in columns]


def _signature(columns, states):
    payload = {
        'columns': _serialize_columns(columns),
        'sources': _source_versions([col.link.task_group_id for col in columns], states),
        'dirty': [(key, value.is_valid, value.full_refresh_required,
                   value.pending_actor_refreshes, value.adapter_version)
                  for key, value in sorted(states.items())],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _policy_signature(group_ids):
    """Authorship affects eligibility even without new attempt publications."""
    authors = list(TaskGroup.authors.through.objects.filter(
        taskgroup_id__in=group_ids,
    ).order_by('taskgroup_id', 'profile_id').values_list('taskgroup_id', 'profile_id'))
    ids = {profile_id for _, profile_id in authors}
    memberships = list(ProfileTeamMembership.objects.filter(profile_id__in=ids).order_by(
        'profile_id', 'team_id',
    ).values_list('profile_id', 'team_id'))
    primary = list(Profile.objects.filter(pk__in=ids).order_by('pk').values_list('pk', 'team_on_id'))
    return hashlib.sha256(json.dumps([authors, memberships, primary]).encode()).hexdigest()


def _incomplete_attempt_groups(game, group_ids):
    if game.pk not in ('alphabetty', 'censorly'):
        return set()
    return set(DailyResultProjection.objects.filter(
        game=game, task_group_id__in=group_ids, attempts_count=0,
    ).values_list('task_group_id', flat=True).distinct())


def _pending_outbox(game, group_ids):
    return DailyResultProjectionRefreshOutbox.objects.filter(
        game=game, task_group_id__in=group_ids,
        status__in=(DailyResultProjectionRefreshOutbox.STATUS_PENDING,
                    DailyResultProjectionRefreshOutbox.STATUS_SENDING),
    )


def request_monthly_snapshot(game, month, columns):
    """Coalesce durable work by month, independently of filters and pagination."""
    group_ids = [col.link.task_group_id for col in columns]
    states = _states(game, group_ids)
    signature = hashlib.sha256(json.dumps([
        _signature(columns, states), _policy_signature(group_ids),
        sorted(_incomplete_attempt_groups(game, group_ids)),
        sorted(_pending_outbox(game, group_ids).values_list('task_group_id', flat=True).distinct()),
    ]).encode()).hexdigest()
    snapshot, _ = DailyAggregateSnapshot.objects.get_or_create(game=game, month=month)
    DailyAggregateSnapshot.objects.filter(pk=snapshot.pk).exclude(
        requested_signature=signature,
    ).update(
        requested_signature=signature, requested_revision=F('requested_revision') + 1,
        requested_at=_requested_at_if_idle(),
    )
    snapshot.refresh_from_db()
    return snapshot, states


def _version_columns(version):
    from games.results.standings.aggregate_leaderboard import ReleaseColumn

    task_ids = [pk for col in version.columns for pk in col.get('task_ids', [])]
    tasks = {task.pk: task for task in Task.objects.filter(pk__in=task_ids).only(
        'id', 'task_group_id', 'answer',
    )}
    columns = []
    missing = set(version.missing_releases)
    for data in version.columns:
        group = SimpleNamespace(result_tasks=[
            tasks[pk] for pk in data.get('task_ids', []) if pk in tasks
        ])
        link = SimpleNamespace(
            pk=data['link_id'], number=data['number'],
            task_group_id=data['group_id'], task_group=group,
        )
        col = ReleaseColumn(
            link=link, label=data['label'], url=data['url'],
            max_score=None if data['group_id'] in missing else data['max_score'],
            published_at=parse_datetime(data['published_at']) if data['published_at'] else None,
            pending=data['group_id'] in missing,
        )
        columns.append(col)
    return columns


def build_saved_monthly_page(request, game, columns, month_context):
    from games.results.standings.aggregate_leaderboard import _build_projection_aggregate_page

    month = month_context[1]
    if month is None:
        return _build_projection_aggregate_page(
            request, game, columns, aggregate_period='month',
            month_context=month_context, saved_monthly=True,
        )
    snapshot, states = request_monthly_snapshot(game, month, columns)
    pinned = request.GET.get('snapshot')
    version = None
    if pinned:
        try:
            version = snapshot.versions.filter(
                pk=uuid.UUID(pinned), published_at__isnull=False,
            ).first()
        except (ValueError, TypeError, AttributeError):
            pass
        if version is None and request.GET.get('partial') == '1':
            raise SnapshotVersionExpired()
    valid_pin = version is not None
    if version is None and snapshot.published_version_id:
        version = snapshot.versions.get(pk=snapshot.published_version_id)
    saved_columns = _version_columns(version) if version else [
        replace(col, max_score=None, pending=True) for col in columns
    ]
    data = _build_projection_aggregate_page(
        request, game, saved_columns, aggregate_period='month',
        month_context=month_context, saved_monthly=True, snapshot_version=version,
    )
    from games.daily.projection import projection_state_is_valid
    dirty = any(not projection_state_is_valid(states.get(col.link.task_group_id), game) for col in columns)
    dirty = dirty or bool(_incomplete_attempt_groups(game, [col.link.task_group_id for col in columns]))
    outbox = _pending_outbox(game, [col.link.task_group_id for col in columns])
    dirty = dirty or outbox.exists()
    pending = snapshot.requested_revision > snapshot.completed_revision
    delayed = bool(snapshot.last_error) or (
        pending and snapshot.requested_at < timezone.now() - timedelta(minutes=5)
    ) or outbox.filter(created_at__lt=timezone.now() - timedelta(minutes=5)).exists()
    data.update({
        'aggregate_snapshot_id': str(version.pk) if version else '',
        'aggregate_snapshot_view': getattr(request, '_aggregate_snapshot_view', ''),
        'aggregate_snapshot_updated_at': version.published_at if version else None,
        'aggregate_snapshot_preparing': version is None,
        'aggregate_snapshot_updating': bool(version and (pending or dirty)),
        'aggregate_snapshot_delayed': delayed,
        'aggregate_snapshot_missing': version.missing_releases if version else [
            col.link.task_group_id for col in columns
        ],
        'aggregate_snapshot_expired': bool(pinned and not valid_pin),
        'aggregate_snapshot_newer': bool(version and snapshot.published_version_id and version.pk != snapshot.published_version_id),
    })
    return data


def request_snapshots_for_release(game_id, group_id):
    """Worker publication invalidates existing boards, never the gameplay write."""
    # A schedule edit can move a release to another month. Touch all requested
    # months for this game; source signatures coalesce unchanged publications.
    DailyAggregateSnapshot.objects.filter(game_id=game_id).filter(
        Q(published_version__isnull=True)
        | Q(published_version__source_versions__has_key=str(group_id))
    ).update(
        requested_revision=F('requested_revision') + 1, requested_at=_requested_at_if_idle(),
    )


def _queue_release_repairs(game, group_ids, states, incomplete):
    from games.daily.projection import projection_state_is_valid, scorer_adapter_version
    from games.realtime.projection import events_enabled, publish_projection_refresh

    pending = []
    for group_id in group_ids:
        state = states.get(group_id)
        if projection_state_is_valid(state, game) and group_id not in incomplete:
            continue
        pending.append(group_id)
        full = (state is None or not state.coverage_complete or state.full_refresh_required
                or state.adapter_version != scorer_adapter_version(game, group_id) or group_id in incomplete)
        if not events_enabled() or not publish_projection_refresh(
            game.pk, group_id, mode='full' if full else 'actor',
        ):
            raise RuntimeError('release refresh queue is unavailable')
    return pending


def _claim(snapshot_id):
    now = timezone.now()
    with transaction.atomic():
        row = DailyAggregateSnapshot.objects.select_for_update().select_related('game').get(pk=snapshot_id)
        if row.completed_revision >= row.requested_revision:
            return None
        if (row.lease_until and row.lease_until > now) or (row.next_attempt_at and row.next_attempt_at > now):
            return None
        row.lease_token = uuid.uuid4()
        row.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        row.attempts += 1
        row.save(update_fields=['lease_token', 'lease_until', 'attempts'])
        return row


def _publish(snapshot):
    from games.results.standings.aggregate_leaderboard import (
        _monthly_columns, _projection_page_cells, _projection_rank_page,
    )

    game = snapshot.game
    columns, selected, *_ = _monthly_columns(game, snapshot.month.strftime('%Y-%m'))
    if selected != snapshot.month:
        # A removed month must not accidentally publish a different month under
        # its key. Leave an existing version visible until schedule reconciliation.
        columns = []
    group_ids = [col.link.task_group_id for col in columns]
    states = _states(game, group_ids)
    sources = _source_versions(group_ids, states)
    incomplete = _incomplete_attempt_groups(game, group_ids)
    usable = [group_id for group_id in group_ids if sources[str(group_id)] is not None and group_id not in incomplete]
    serialized_columns = _serialize_columns(columns)
    current = DailyAggregateSnapshotVersion.objects.filter(pk=snapshot.published_version_id).first()
    prior_columns = {col['group_id']: col for col in current.columns} if current else {}
    from games.daily.projection import scorer_adapter_version
    for index, col in enumerate(serialized_columns):
        group_id = col['group_id']
        state = states.get(group_id)
        if group_id not in usable or state is None:
            continue
        if state.full_refresh_required or state.adapter_version != scorer_adapter_version(game, group_id):
            # Keep the maximum and task metadata that belong to the old scores
            # while a structural/scoring change is being rebuilt.
            if (current and group_id in prior_columns
                    and group_id not in current.missing_releases
                    and current.source_versions.get(str(group_id)) == sources[str(group_id)]):
                serialized_columns[index] = prior_columns[group_id]
            else:
                usable.remove(group_id)
    missing = [group_id for group_id in group_ids if group_id not in usable]
    policy_signature = _policy_signature(group_ids)
    repair_error = ''
    try:
        pending = _queue_release_repairs(game, group_ids, states, incomplete)
    except RuntimeError as exc:
        pending = group_ids
        repair_error = str(exc)
        logger.warning('aggregate_snapshot_repair_queue_failed game=%s month=%s', game.pk, snapshot.month)

    if group_ids and not usable:
        DailyAggregateSnapshot.objects.filter(pk=snapshot.pk, lease_token=snapshot.lease_token).update(
            lease_token=None, lease_until=None, requested_at=timezone.now(),
            next_attempt_at=timezone.now() + timedelta(seconds=RETRY_SECONDS),
            last_error=repair_error,
        )
        return 'waiting_for_releases'

    unchanged = bool(current and current.columns == serialized_columns
                     and current.source_versions == sources
                     and current.dependency_signature == policy_signature
                     and current.missing_releases == missing)
    if unchanged:
        _finish_publication(snapshot, current, group_ids, sources, policy_signature, pending, repair_error)
        return 'unchanged'

    version = DailyAggregateSnapshotVersion.objects.create(
        snapshot=snapshot, columns=serialized_columns, dependency_signature=policy_signature,
        source_versions=sources, missing_releases=missing,
    )
    try:
        page = 1
        while True:
            renewed = DailyAggregateSnapshot.objects.filter(
                pk=snapshot.pk, lease_token=snapshot.lease_token,
            ).update(lease_until=timezone.now() + timedelta(seconds=LEASE_SECONDS))
            if not renewed:
                raise RuntimeError('snapshot refresh lease was replaced')
            values, total, _ = _projection_rank_page(
                game, usable, page, snapshot_build=True, page_size=BUILD_PAGE_SIZE,
            )
            if not values:
                break
            cells = _projection_page_cells(game, usable, values, include_hidden=True)
            DailyAggregateSnapshotRow.objects.bulk_create([
                DailyAggregateSnapshotRow(
                    version=version, actor_type=row['actor_type'], actor_key=row['actor_key'],
                    team_id=row['team_id'], user_id=row['user_id'], anon_key=row['anon_key'],
                    score=row['window_score'], attempts_count=row['attempts_count'],
                    played_count=row['played_count'], total_time_ms=row['total_time_ms'],
                    used_fallback_time=bool(row.get('used_fallback_time')),
                    cells={str(key): float(value) for key, value in cells[(row['actor_type'], row['actor_key'])].items()},
                ) for row in values
            ], batch_size=50)
            if page * BUILD_PAGE_SIZE >= total:
                break
            page += 1
        _finish_publication(snapshot, version, group_ids, sources, policy_signature, pending, repair_error)
        logger.info('aggregate_snapshot_published game=%s month=%s version=%s missing=%s',
                    game.pk, snapshot.month, version.pk, len(missing))
        return 'published'
    except Exception:
        version.delete()
        raise


def _finish_publication(snapshot, version, group_ids, sources, policy_signature, pending, error):
    with transaction.atomic():
        locked = DailyAggregateSnapshot.objects.select_for_update().get(pk=snapshot.pk)
        if locked.lease_token != snapshot.lease_token:
            raise RuntimeError('snapshot refresh lease was replaced')
        current_sources = _source_versions(group_ids, _states(snapshot.game, group_ids, lock=True))
        if current_sources != sources or _policy_signature(group_ids) != policy_signature:
            raise RuntimeError('snapshot source versions changed during build')
        now = timezone.now()
        if version.published_at is None:
            version.published_at = now
            version.save(update_fields=['published_at'])
        locked.published_version = version
        locked.completed_revision = snapshot.requested_revision
        locked.requested_revision = max(locked.requested_revision, snapshot.requested_revision + bool(pending))
        locked.lease_token = None
        locked.lease_until = None
        locked.attempts = 0
        locked.last_error = error
        locked.checked_at = now
        locked.requested_at = now
        locked.next_attempt_at = now + timedelta(seconds=RETRY_SECONDS) if pending else None
        locked.save(update_fields=[
            'published_version', 'completed_revision', 'requested_revision', 'lease_token',
            'lease_until', 'attempts', 'last_error', 'checked_at', 'next_attempt_at', 'requested_at',
        ])


def refresh_monthly_snapshot(snapshot_id):
    snapshot = _claim(snapshot_id)
    if snapshot is None:
        return 'skipped'
    try:
        return _publish(snapshot)
    except Exception as exc:
        delay = min(300, 2 ** min(snapshot.attempts, 8))
        DailyAggregateSnapshot.objects.filter(pk=snapshot.pk, lease_token=snapshot.lease_token).update(
            lease_token=None, lease_until=None, next_attempt_at=timezone.now() + timedelta(seconds=delay),
            requested_at=timezone.now(),
            last_error='{}: {}'.format(type(exc).__name__, exc)[:2000],
        )
        logger.exception('aggregate_snapshot_failed game=%s month=%s', snapshot.game_id, snapshot.month)
        return 'failed'


def reconcile_monthly_snapshots(*, limit=2):
    """Called by the existing background reconcile queue; never by HTTP."""
    now = timezone.now()
    # Revisit a bounded set even without notifications (worker crash, author /
    # visibility edits, timing-only changes). This also renews source signatures.
    check_rows = list(DailyAggregateSnapshot.objects.filter(
        checked_at__lt=now - timedelta(minutes=5),
    ).select_related('game').order_by('checked_at')[:limit])
    from games.results.standings.aggregate_leaderboard import _monthly_columns
    for row in check_rows:
        columns, selected, *_ = _monthly_columns(row.game, row.month.strftime('%Y-%m'))
        request_monthly_snapshot(row.game, row.month, columns if selected == row.month else [])
        DailyAggregateSnapshot.objects.filter(pk=row.pk).update(checked_at=now)
    due = DailyAggregateSnapshot.objects.filter(
        requested_revision__gt=F('completed_revision'),
    ).filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)).filter(
        Q(lease_until__isnull=True) | Q(lease_until__lte=now),
    ).order_by('requested_at').values_list('pk', flat=True)[:limit]
    result = [refresh_monthly_snapshot(pk) for pk in list(due)]
    # Keep one day's versions for pinned progressive requests. Never delete the
    # active version; also collect candidates abandoned by killed workers.
    active = DailyAggregateSnapshot.objects.exclude(published_version=None).values('published_version_id')
    stale = DailyAggregateSnapshotVersion.objects.filter(
        Q(published_at__lt=now - VERSION_RETENTION)
        | Q(published_at__isnull=True, created_at__lt=now - timedelta(seconds=LEASE_SECONDS * 2)),
    ).exclude(pk__in=active).order_by('created_at').values_list('pk', flat=True)[:10]
    DailyAggregateSnapshotVersion.objects.filter(pk__in=list(stale)).delete()
    return result
