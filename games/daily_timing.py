"""Active solving time for official daily games (ladder, alphabetty, salad).

Canonical duration lives on ``DailySolveTiming``. Absence of a row means the
legacy first-to-last attempt formula. Timing version 1 never backfills that
legacy gap into active time.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from games.daily_section import is_daily_timing_game
from games.models import DailySolveTiming
from games.share_result import elapsed_seconds_from_attempts, format_elapsed

TIMING_VERSION_ACTIVE = DailySolveTiming.TIMING_VERSION_ACTIVE
STATUS_RUNNING = DailySolveTiming.STATUS_RUNNING
STATUS_AUTO_PAUSED = DailySolveTiming.STATUS_AUTO_PAUSED
STATUS_MANUALLY_PAUSED = DailySolveTiming.STATUS_MANUALLY_PAUSED
STATUS_COMPLETED = DailySolveTiming.STATUS_COMPLETED

ACTION_START = 'start'
ACTION_RESUME = 'resume'
ACTION_HEARTBEAT = 'heartbeat'
ACTION_AUTO_PAUSE = 'auto_pause'
ACTION_PAUSE = 'pause'
ACTION_COMPLETE = 'complete'

HEARTBEAT_MAX_CREDIT_MS = 60_000
LEASE_STALE_MS = 45_000
APPLIED_EVENT_LIMIT = 64
MAX_ACCUMULATED_MS = 12 * 60 * 60 * 1000  # 12h hard cap for one daily solve
_UNSET = object()

MUTATING_ACTIONS = {
    ACTION_START,
    ACTION_RESUME,
    ACTION_HEARTBEAT,
    ACTION_AUTO_PAUSE,
    ACTION_PAUSE,
    ACTION_COMPLETE,
}


def actor_filter(*, user=None, anon_key=None) -> dict | None:
    if user is not None:
        return {'user': user, 'anon_key__isnull': True}
    if anon_key:
        return {'anon_key': str(anon_key), 'user__isnull': True}
    return None


def lookup_timing(*, game, task_group, user=None, anon_key=None) -> DailySolveTiming | None:
    filters = actor_filter(user=user, anon_key=anon_key)
    if filters is None or game is None or task_group is None:
        return None
    return DailySolveTiming.objects.filter(
        game=game,
        task_group=task_group,
        **filters,
    ).first()


def empty_snapshot() -> dict:
    return {
        'timing_version': TIMING_VERSION_ACTIVE,
        'status': STATUS_AUTO_PAUSED,
        'accumulated_ms': 0,
        'committed_ms': 0,
        'frozen_ms': None,
        'is_authoritative': False,
        'manually_paused': False,
        'completed': False,
        'exists': False,
    }


def snapshot(row: DailySolveTiming | None, *, now=None, session_id=None) -> dict:
    if row is None:
        return empty_snapshot()
    now = now or timezone.now()
    open_ms = 0
    if row.status == STATUS_RUNNING:
        open_ms = _open_interval_ms(row, now)
    display_ms = int(row.accumulated_ms) + open_ms
    if row.status == STATUS_COMPLETED and row.frozen_ms is not None:
        display_ms = int(row.frozen_ms)
    sid = _as_uuid(session_id) if session_id else None
    return {
        'timing_version': int(row.timing_version or TIMING_VERSION_ACTIVE),
        'status': row.status,
        'accumulated_ms': display_ms,
        'committed_ms': int(row.accumulated_ms),
        'frozen_ms': int(row.frozen_ms) if row.frozen_ms is not None else None,
        'is_authoritative': bool(
            sid
            and row.active_session_id
            and sid == row.active_session_id
            and row.status == STATUS_RUNNING
        ),
        'manually_paused': row.status == STATUS_MANUALLY_PAUSED,
        'completed': row.status == STATUS_COMPLETED,
        'exists': True,
    }


def canonical_elapsed_seconds(
    *,
    game,
    task_group=None,
    user=None,
    anon_key=None,
    attempts=None,
    team=None,
    timing_row=_UNSET,
) -> int:
    """Seconds to display after a solve. Team / non-daily / legacy stay first-to-last."""
    if team is not None or not is_daily_timing_game(getattr(game, 'id', None)):
        return elapsed_seconds_from_attempts(attempts)
    if timing_row is _UNSET:
        tg = task_group
        if tg is None and attempts:
            task = getattr(attempts[0], 'task', None)
            tg = getattr(task, 'task_group', None)
        row = lookup_timing(game=game, task_group=tg, user=user, anon_key=anon_key)
    else:
        row = timing_row
    if row is None or int(row.timing_version or 0) < TIMING_VERSION_ACTIVE:
        return elapsed_seconds_from_attempts(attempts)
    if row.status == STATUS_COMPLETED and row.frozen_ms is not None:
        return max(0, int(row.frozen_ms) // 1000)
    return max(0, int(row.accumulated_ms) // 1000)


def canonical_elapsed_label(**kwargs) -> str:
    return format_elapsed(canonical_elapsed_seconds(**kwargs))


def elapsed_label_for_complete_attempts(
    attempts, *, game=None, task=None, user=None, anon_key=None, timing_row=_UNSET,
) -> str:
    if not attempts:
        return format_elapsed(0)
    first = attempts[0]
    game = game or getattr(first, 'game', None)
    if user is None and not anon_key:
        user = getattr(first, 'user', None)
    if not anon_key:
        anon_key = getattr(first, 'anon_key', None)
    tg = None
    if timing_row is _UNSET:
        task = task or getattr(first, 'task', None)
        tg = getattr(task, 'task_group', None)
    return canonical_elapsed_label(
        game=game,
        task_group=tg,
        user=user,
        anon_key=anon_key,
        attempts=attempts,
        timing_row=timing_row,
    )


@transaction.atomic
def apply_timing_event(
    *,
    game,
    task_group,
    user=None,
    anon_key=None,
    action: str,
    session_id,
    event_id: str,
    seq: int,
    claimed_ms=None,
    now=None,
    create: bool = True,
) -> dict:
    now = now or timezone.now()
    action = (action or '').strip()
    if action not in MUTATING_ACTIONS:
        return empty_snapshot()
    filters = actor_filter(user=user, anon_key=anon_key)
    if filters is None or game is None or task_group is None:
        return empty_snapshot()
    if not is_daily_timing_game(getattr(game, 'id', None)):
        return empty_snapshot()

    qs = DailySolveTiming.objects.select_for_update().filter(
        game=game,
        task_group=task_group,
        **filters,
    )
    row = qs.first()
    if row is None:
        if not create or action not in (ACTION_START, ACTION_RESUME):
            return empty_snapshot()
        create_kwargs = {
            'game': game,
            'task_group': task_group,
            'timing_version': TIMING_VERSION_ACTIVE,
            'status': STATUS_AUTO_PAUSED,
            'accumulated_ms': 0,
        }
        if user is not None:
            create_kwargs['user'] = user
        else:
            create_kwargs['anon_key'] = str(anon_key)
        try:
            with transaction.atomic():
                row = DailySolveTiming.objects.create(**create_kwargs)
        except IntegrityError:
            row = (
                DailySolveTiming.objects.select_for_update()
                .filter(game=game, task_group=task_group, **filters)
                .first()
            )
            if row is None:
                return empty_snapshot()
        else:
            row = DailySolveTiming.objects.select_for_update().get(pk=row.pk)

    _apply_to_row(
        row,
        action=action,
        session_id=session_id,
        event_id=event_id,
        seq=seq,
        claimed_ms=claimed_ms,
        now=now,
    )
    return snapshot(row, now=now, session_id=session_id)


@transaction.atomic
def complete_daily_timing(*, game, task_group, user=None, anon_key=None, team=None, now=None) -> dict | None:
    """Freeze an existing v1 row. Do not create a row — that would rewrite legacy solves."""
    if team is not None or not is_daily_timing_game(getattr(game, 'id', None)):
        return None
    now = now or timezone.now()
    filters = actor_filter(user=user, anon_key=anon_key)
    if filters is None or task_group is None:
        return None
    row = (
        DailySolveTiming.objects.select_for_update()
        .filter(game=game, task_group=task_group, **filters)
        .first()
    )
    if row is None:
        return None
    _apply_to_row(
        row,
        action=ACTION_COMPLETE,
        session_id=row.active_session_id,
        event_id='complete:{}'.format(row.pk),
        seq=max(int(row.last_seq or 0) + 1, 1),
        claimed_ms=None,
        now=now,
    )
    return snapshot(row, now=now)


def merge_timing_rows(target: DailySolveTiming, source: DailySolveTiming) -> DailySolveTiming:
    """Combine two rows for the same daily solve after anon→user or account merge."""
    if target.pk == source.pk:
        return target
    target_completed = target.status == STATUS_COMPLETED
    source_completed = source.status == STATUS_COMPLETED
    if target_completed or source_completed:
        keep = target if target_completed else source
        other = source if keep.pk == target.pk else target
        frozen = keep.frozen_ms
        if frozen is None:
            frozen = keep.accumulated_ms
        if other.status == STATUS_COMPLETED and other.frozen_ms is not None:
            # Same person, two completed rows: keep the already frozen value
            # from the surviving identity, do not sum overlapping sessions.
            if keep.pk != target.pk:
                frozen = other.frozen_ms if other.pk == target.pk else frozen
        target.status = STATUS_COMPLETED
        target.frozen_ms = int(frozen or 0)
        target.accumulated_ms = int(frozen or 0)
        target.completed_at = keep.completed_at or other.completed_at
        target.active_session_id = None
        target.interval_started_at = None
        target.last_heartbeat_at = keep.last_heartbeat_at or other.last_heartbeat_at
        target.timing_version = max(
            int(target.timing_version or 0),
            int(source.timing_version or 0),
            TIMING_VERSION_ACTIVE,
        )
    else:
        target.accumulated_ms = max(int(target.accumulated_ms or 0), int(source.accumulated_ms or 0))
        if STATUS_MANUALLY_PAUSED in (target.status, source.status):
            target.status = STATUS_MANUALLY_PAUSED
        else:
            target.status = STATUS_AUTO_PAUSED
        target.active_session_id = None
        target.interval_started_at = None
        target.frozen_ms = None
        target.completed_at = None
        target.timing_version = max(
            int(target.timing_version or 0),
            int(source.timing_version or 0),
            TIMING_VERSION_ACTIVE,
        )
    target.last_seq = max(int(target.last_seq or 0), int(source.last_seq or 0))
    ids = list(target.applied_event_ids or []) + list(source.applied_event_ids or [])
    target.applied_event_ids = ids[-APPLIED_EVENT_LIMIT:]
    target.last_event_id = target.last_event_id or source.last_event_id
    target.save()
    source.delete()
    return target


def _owns_lease(row: DailySolveTiming, sid) -> bool:
    return bool(sid and row.active_session_id and sid == row.active_session_id)


def _seq_stale_for_owner(row: DailySolveTiming, sid, seq) -> bool:
    """``seq`` is per session. A new tab starts at 1 and must still be able to take over."""
    if not _owns_lease(row, sid):
        return False
    return seq <= int(row.last_seq or 0)


def _apply_to_row(row: DailySolveTiming, *, action, session_id, event_id, seq, claimed_ms, now):
    if row.status == STATUS_COMPLETED:
        return

    event_id = str(event_id or '').strip()[:64]
    applied = list(row.applied_event_ids or [])
    if event_id and event_id in applied:
        return
    try:
        seq = int(seq or 0)
    except (TypeError, ValueError):
        return

    sid = _as_uuid(session_id)
    claimed = _parse_claimed_ms(claimed_ms)

    if action == ACTION_COMPLETE:
        if _owns_lease(row, sid):
            if _seq_stale_for_owner(row, sid, seq):
                return
            _close_own_interval(row, sid, claimed, now, cap_ms=MAX_ACCUMULATED_MS)
        else:
            _close_foreign_interval(row, now)
        row.status = STATUS_COMPLETED
        row.frozen_ms = min(MAX_ACCUMULATED_MS, max(0, int(row.accumulated_ms)))
        row.accumulated_ms = row.frozen_ms
        row.completed_at = now
        row.active_session_id = None
        row.interval_started_at = None
        _remember_event(row, event_id, seq)
        row.save()
        return

    if action == ACTION_PAUSE:
        if _owns_lease(row, sid) and _seq_stale_for_owner(row, sid, seq):
            return
        if _owns_lease(row, sid):
            _close_own_interval(row, sid, claimed, now)
        else:
            # Explicit pause from any tab/device stops the current lease.
            _close_foreign_interval(row, now)
        row.status = STATUS_MANUALLY_PAUSED
        row.active_session_id = None
        row.interval_started_at = None
        _remember_event(row, event_id, seq)
        row.save()
        return

    if action == ACTION_AUTO_PAUSE:
        if row.status == STATUS_MANUALLY_PAUSED:
            return
        if not _owns_lease(row, sid):
            # Hidden/crashed tab must not steal the lease from another device.
            return
        if _seq_stale_for_owner(row, sid, seq):
            return
        _close_own_interval(row, sid, claimed, now)
        if row.status != STATUS_COMPLETED:
            row.status = STATUS_AUTO_PAUSED
            row.active_session_id = None
            row.interval_started_at = None
        _remember_event(row, event_id, seq)
        row.save()
        return

    if action == ACTION_HEARTBEAT:
        if not _owns_lease(row, sid) or row.status != STATUS_RUNNING:
            return
        if _seq_stale_for_owner(row, sid, seq):
            return
        _fold_running(row, claimed, now)
        _remember_event(row, event_id, seq)
        row.save()
        return

    if action in (ACTION_START, ACTION_RESUME):
        if row.status == STATUS_MANUALLY_PAUSED and action != ACTION_RESUME:
            return
        if not sid:
            return
        if _seq_stale_for_owner(row, sid, seq):
            return
        _takeover_or_continue(row, sid, claimed, now)
        row.status = STATUS_RUNNING
        _remember_event(row, event_id, seq)
        row.save()


def _takeover_or_continue(row: DailySolveTiming, sid: UUID, claimed_ms, now):
    if row.active_session_id == sid and row.status == STATUS_RUNNING:
        _fold_running(row, claimed_ms, now)
        return
    _close_foreign_interval(row, now)
    row.active_session_id = sid
    row.interval_started_at = now
    row.last_heartbeat_at = now


def _close_own_interval(row: DailySolveTiming, sid, claimed_ms, now, *, cap_ms=HEARTBEAT_MAX_CREDIT_MS):
    if row.status != STATUS_RUNNING:
        row.active_session_id = None
        row.interval_started_at = None
        return
    if sid and row.active_session_id and sid != row.active_session_id:
        return
    _fold_running(row, claimed_ms, now, restart=False, cap_ms=cap_ms)
    row.active_session_id = None
    row.interval_started_at = None


def _close_foreign_interval(row: DailySolveTiming, now):
    if row.status != STATUS_RUNNING or row.interval_started_at is None:
        row.active_session_id = None
        row.interval_started_at = None
        return
    end = row.last_heartbeat_at or row.interval_started_at
    credit = _ms_between(row.interval_started_at, end)
    credit = min(credit, HEARTBEAT_MAX_CREDIT_MS)
    row.accumulated_ms = min(MAX_ACCUMULATED_MS, int(row.accumulated_ms) + credit)
    row.active_session_id = None
    row.interval_started_at = None


def _fold_running(row: DailySolveTiming, claimed_ms, now, *, restart=True, cap_ms=HEARTBEAT_MAX_CREDIT_MS):
    if row.interval_started_at is None:
        if restart:
            row.interval_started_at = now
            row.last_heartbeat_at = now
        return
    credit = _open_interval_ms(row, now, claimed_ms=claimed_ms, cap_ms=cap_ms)
    row.accumulated_ms = min(MAX_ACCUMULATED_MS, int(row.accumulated_ms) + credit)
    row.last_heartbeat_at = now
    if restart:
        row.interval_started_at = now
    else:
        row.interval_started_at = None


def _open_interval_ms(row: DailySolveTiming, now, claimed_ms=None, cap_ms=HEARTBEAT_MAX_CREDIT_MS) -> int:
    if row.interval_started_at is None:
        return 0
    credit = _ms_between(row.interval_started_at, now)
    credit = min(max(0, credit), cap_ms)
    if claimed_ms is not None:
        credit = min(credit, max(0, int(claimed_ms)))
    return credit


def _ms_between(start, end) -> int:
    if start is None or end is None or end < start:
        return 0
    delta: timedelta = end - start
    return max(0, int(delta.total_seconds() * 1000))


def _parse_claimed_ms(value) -> int | None:
    if value is None or value == '':
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms < 0:
        return 0
    return min(ms, HEARTBEAT_MAX_CREDIT_MS)


def _as_uuid(value):
    if value is None or value == '':
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _remember_event(row: DailySolveTiming, event_id: str, seq: int):
    row.last_seq = seq
    if event_id:
        row.last_event_id = event_id
        ids = [item for item in (row.applied_event_ids or []) if item != event_id]
        ids.append(event_id)
        row.applied_event_ids = ids[-APPLIED_EVENT_LIMIT:]


def timing_rows_for_task_groups(*, game, task_group_ids, user=None, anon_key=None, team=None):
    if team is not None or not is_daily_timing_game(getattr(game, 'id', None)):
        return {}
    filters = actor_filter(user=user, anon_key=anon_key)
    if filters is None or not task_group_ids:
        return {}
    rows = DailySolveTiming.objects.filter(
        game=game,
        task_group_id__in=list(task_group_ids),
        **filters,
    )
    return {row.task_group_id: row for row in rows}
