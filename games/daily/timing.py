"""Active solving time for one actor and one task group.

Canonical duration lives on ``DailySolveTiming``. The legacy UI may still use
its historical fallback for personal/anonymous rows, but public leaderboard
timing never infers a duration when this row is absent. Version 1 never
backfills that gap.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID, uuid4

from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from games.db_retry import is_mysql_retryable_lock_error, log_lock_retry
from games.middleware.request_timing import timing_phase
from games.models import DailySolveTiming, DailySolveTimingSession, DailyTimingEvent
from games.results.share import elapsed_seconds_from_attempts, format_elapsed

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
MYSQL_DEADLOCK_ERRNO = 1213
TIMING_DEADLOCK_ATTEMPTS = 3
_UNSET = object()

# Keep the established logger name while the implementation moves namespaces.
logger = logging.getLogger('games.daily_timing')

MUTATING_ACTIONS = {
    ACTION_START,
    ACTION_RESUME,
    ACTION_HEARTBEAT,
    ACTION_AUTO_PAUSE,
    ACTION_PAUSE,
    ACTION_COMPLETE,
}


def _has_prior_statistical_activity(*, game, task_group, actor, replay_slot=None):
    """Do not attach a fresh clock to a legacy first play with unknown start."""
    if replay_slot is not None:
        return False
    from games.models import Attempt, HintAttempt

    identity = {key: value for key, value in actor.items() if key != 'replay_slot'}
    attempts_exist = Attempt.manager.filter(
        game=game, task__task_group=task_group, skip=False,
        replay_slot__isnull=True, **identity,
    ).exists()
    if attempts_exist:
        return True
    return HintAttempt.objects.filter(
        hint__task__task_group=task_group, replay_slot__isnull=True, **identity,
    ).exists()


def actor_filter(*, team=None, user=None, anon_key=None, replay_slot=None) -> dict | None:
    identities = sum(value is not None and value != '' for value in (team, user, anon_key))
    if identities != 1:
        return None
    if team is not None:
        return {'team': team, 'user__isnull': True, 'anon_key__isnull': True, 'replay_slot': replay_slot}
    if user is not None:
        return {'user': user, 'team__isnull': True, 'anon_key__isnull': True, 'replay_slot': replay_slot}
    if anon_key:
        return {'anon_key': str(anon_key), 'user__isnull': True, 'team__isnull': True, 'replay_slot': replay_slot}
    return None


def lookup_timing(*, game, task_group, team=None, user=None, anon_key=None, replay_slot=None) -> DailySolveTiming | None:
    filters = actor_filter(team=team, user=user, anon_key=anon_key, replay_slot=replay_slot)
    if filters is None or game is None or task_group is None:
        return None
    return DailySolveTiming.objects.filter(
        game=game,
        task_group=task_group,
        **filters,
    ).first()


def _lock_existing_timing(logical_qs):
    """Lock an existing timing row through its primary key.

    The logical actor/release lookup can use one of several secondary
    indexes.  Using that lookup directly with ``FOR UPDATE`` allowed two
    identical heartbeats to acquire a secondary-index record lock and the
    primary-key record lock in opposite orders.  First finding the immutable
    row id without a lock, then taking the canonical primary-key lock, gives
    every existing-row mutation the same InnoDB lock order.

    A row may disappear between the two reads during maintenance cleanup;
    callers treat that as the normal missing-row case and apply their
    existing create/no-op policy.
    """
    row_id = logical_qs.order_by('pk').values_list('pk', flat=True).first()
    if row_id is None:
        return None
    return DailySolveTiming.objects.select_for_update().filter(pk=row_id).first()


def empty_snapshot() -> dict:
    return {
        'timing_version': TIMING_VERSION_ACTIVE,
        'status': STATUS_AUTO_PAUSED,
        'accumulated_ms': 0,
        'committed_ms': 0,
        'frozen_ms': None,
        'is_authoritative': False,
        'session_active': False,
        'session_status': None,
        'active_sessions_count': 0,
        'manually_paused': False,
        'completed': False,
        'exists': False,
    }


class _TimingReducerRow:
    """Small in-memory row accepted by the legacy personal reducer helpers."""

    team_id = None

    def __init__(self):
        self.status = STATUS_AUTO_PAUSED
        self.accumulated_ms = 0
        self.frozen_ms = None
        self.active_session_id = None
        self.interval_started_at = None
        self.last_heartbeat_at = None
        self.last_seq = 0
        self.last_event_id = ''
        self.applied_event_ids = []
        self.completed_at = None
        self.timing_version = TIMING_VERSION_ACTIVE

    def save(self, *args, **kwargs):
        # The reducer is deliberately side-effect free; _apply_to_row was
        # written for the compatibility ORM read-model and calls save().
        return None


def reduce_personal_timing_events(events, *, now=None) -> dict:
    """Fold personal/anonymous append-only events into a timing snapshot.

    Personal actors use the same union-of-session semantics as teams.  The
    function never touches the database and is therefore safe for workers,
    audits, and eventual read-model rebuilds.
    """
    personal_events = [
        event for event in events
        if _event_value(event, 'team_id') is None
        and _event_value(event, 'team') is None
    ]
    result = reduce_team_timing_events(personal_events, now=now)
    result.update({
        'event_count': len(events),
        'skipped_team_event_count': len(events) - len(personal_events),
        'last_seq': max(
            [int(_event_value(event, 'seq') or 0) for event in personal_events] or [0]
        ),
    })
    return result


def _event_value(event, name):
    if isinstance(event, dict):
        return event.get(name)
    return getattr(event, name, None)


def _event_time(event, *, fallback=None):
    """Use server arrival time first; client time is only an ordering hint."""
    return (
        _event_value(event, 'occurred_at')
        or _event_value(event, 'client_occurred_at')
        or fallback
        or timezone.now()
    )


def reduce_team_timing_events(events, *, now=None) -> dict:
    """Fold team events by taking the union of active session intervals."""
    ordered = sorted(
        events,
        key=lambda event: (
            _event_time(event, fallback=now),
            int(_event_value(event, 'seq') or 0),
            str(_event_value(event, 'event_id') or ''),
        ),
    )
    sessions = {}
    accumulated_ms = 0
    interval_started_at = None
    status = STATUS_AUTO_PAUSED
    frozen_ms = None
    completed_at = None
    applied = 0

    def close_interval(end):
        nonlocal accumulated_ms, interval_started_at
        if interval_started_at is not None:
            accumulated_ms = min(
                MAX_ACCUMULATED_MS,
                accumulated_ms + _ms_between(interval_started_at, end),
            )
        interval_started_at = None

    def expire(at):
        nonlocal status
        cutoff = at - timedelta(milliseconds=LEASE_STALE_MS)
        running = [session for session in sessions.values() if session['status'] == STATUS_RUNNING]
        for session in running:
            if session['last_heartbeat_at'] and session['last_heartbeat_at'] < cutoff:
                session['status'] = DailySolveTimingSession.STATUS_PAUSED
                session['close_reason'] = 'stale'
        active = [session for session in running if session['status'] == STATUS_RUNNING]
        if not active and running:
            end = max(
                (session['last_heartbeat_at'] for session in running if session['last_heartbeat_at']),
                default=at,
            )
            close_interval(end)
        return active

    for event in ordered:
        action = _event_value(event, 'action')
        if action not in MUTATING_ACTIONS:
            continue
        at = _event_value(event, 'occurred_at') or now or timezone.now()
        expire(at)
        sid = str(_event_value(event, 'session_id') or '')
        if not sid:
            continue
        session = sessions.setdefault(sid, {
            'status': DailySolveTimingSession.STATUS_PAUSED,
            'started_at': None,
            'last_heartbeat_at': None,
            'last_seq': 0,
            'event_ids': set(),
            'close_reason': '',
        })
        event_id = str(_event_value(event, 'event_id') or '')[:64]
        seq = int(_event_value(event, 'seq') or 0)
        if (event_id and event_id in session['event_ids']) or seq <= session['last_seq']:
            continue
        session['event_ids'].add(event_id)
        session['last_seq'] = seq
        applied += 1

        if action == ACTION_COMPLETE:
            active = [item for item in sessions.values() if item['status'] == STATUS_RUNNING]
            if active:
                fresh_cutoff = at - timedelta(milliseconds=LEASE_STALE_MS)
                if any(item['last_heartbeat_at'] and item['last_heartbeat_at'] >= fresh_cutoff for item in active):
                    close_interval(at)
                else:
                    close_interval(max(item['last_heartbeat_at'] for item in active if item['last_heartbeat_at']))
            for item in active:
                item['status'] = DailySolveTimingSession.STATUS_CLOSED
                item['close_reason'] = 'completed'
            status = STATUS_COMPLETED
            frozen_ms = min(MAX_ACCUMULATED_MS, max(0, accumulated_ms))
            accumulated_ms = frozen_ms
            completed_at = at
            continue

        if status == STATUS_COMPLETED:
            continue
        if action == ACTION_HEARTBEAT:
            if session['status'] == STATUS_RUNNING:
                session['last_heartbeat_at'] = at
                status = STATUS_RUNNING
            continue
        if action in (ACTION_PAUSE, ACTION_AUTO_PAUSE):
            if session['status'] != STATUS_RUNNING:
                continue
            session['status'] = DailySolveTimingSession.STATUS_PAUSED
            session['close_reason'] = 'manual' if action == ACTION_PAUSE else 'auto'
            if not any(item['status'] == STATUS_RUNNING for item in sessions.values()):
                close_interval(at)
                status = STATUS_MANUALLY_PAUSED if action == ACTION_PAUSE else STATUS_AUTO_PAUSED
            continue
        if action in (ACTION_START, ACTION_RESUME):
            if action == ACTION_START and session['close_reason'] == 'manual':
                continue
            if not any(item['status'] == STATUS_RUNNING for item in sessions.values()):
                interval_started_at = at
            session['status'] = STATUS_RUNNING
            session['started_at'] = session['started_at'] or at
            session['last_heartbeat_at'] = at
            session['close_reason'] = ''
            status = STATUS_RUNNING

    display_ms = accumulated_ms
    if status == STATUS_RUNNING and interval_started_at is not None:
        display_ms += _ms_between(interval_started_at, now or timezone.now())
    return {
        **empty_snapshot(),
        'status': status,
        'accumulated_ms': min(MAX_ACCUMULATED_MS, display_ms),
        'committed_ms': accumulated_ms,
        'frozen_ms': frozen_ms,
        'session_active': any(item['status'] == STATUS_RUNNING for item in sessions.values()),
        'active_sessions_count': sum(item['status'] == STATUS_RUNNING for item in sessions.values()),
        'completed': status == STATUS_COMPLETED,
        'event_count': len(ordered),
        'applied_event_count': applied,
        'session_count': len(sessions),
        'completed_at': completed_at,
    }


def snapshot(row: DailySolveTiming | None, *, now=None, session_id=None) -> dict:
    if row is None:
        return empty_snapshot()
    now = now or timezone.now()
    open_ms = 0
    session = None
    sessionized = _uses_session_leases(row, session_id=session_id)
    if sessionized:
        session = _team_session_for_snapshot(row, session_id)
        open_ms = _team_open_interval_ms(row, now)
    elif row.status == STATUS_RUNNING:
        open_ms = _open_interval_ms(row, now)
    display_ms = int(row.accumulated_ms) + open_ms
    if row.status == STATUS_COMPLETED and row.frozen_ms is not None:
        display_ms = int(row.frozen_ms)
    sid = _as_uuid(session_id) if session_id else None
    session_running = bool(session and session.status == DailySolveTimingSession.STATUS_RUNNING)
    return {
        'timing_version': int(row.timing_version or TIMING_VERSION_ACTIVE),
        'status': row.status,
        'accumulated_ms': display_ms,
        'committed_ms': int(row.accumulated_ms),
        'frozen_ms': int(row.frozen_ms) if row.frozen_ms is not None else None,
        'is_authoritative': session_running if sessionized else bool(
            sid
            and row.active_session_id
            and sid == row.active_session_id
            and row.status == STATUS_RUNNING
        ),
        'session_active': session_running,
        'session_status': session.status if session else None,
        'active_sessions_count': int(row.active_sessions_count or 0) if sessionized else 0,
        'manually_paused': (
            bool(session and session.close_reason == 'manual')
            if sessionized else row.status == STATUS_MANUALLY_PAUSED
        ),
        'completed': row.status == STATUS_COMPLETED,
        'exists': True,
    }


def _team_session_for_snapshot(row: DailySolveTiming, session_id):
    sid = _as_uuid(session_id) if session_id else None
    if not sid:
        return None
    return DailySolveTimingSession.objects.filter(timing=row, session_id=sid).first()


def _uses_session_leases(row: DailySolveTiming, *, session_id=None) -> bool:
    """Whether this row uses independent tab/device leases.

    Existing personal rows are converted lazily on their next mutation, so a
    legacy snapshot still needs the old single-lease path until then.
    """
    deferred = row.get_deferred_fields() if hasattr(row, 'get_deferred_fields') else set()
    if 'team' not in deferred and row.team_id:
        return True
    if 'active_sessions_count' not in deferred and int(row.active_sessions_count or 0) > 0:
        return True
    if 'team_interval_started_at' not in deferred and row.team_interval_started_at is not None:
        return True
    if session_id is None:
        return False
    sessions = getattr(row, 'sessions', None)
    sid = _as_uuid(session_id)
    return bool(sessions and sid and sessions.filter(session_id=sid).exists())


def _team_open_interval_ms(row: DailySolveTiming, now) -> int:
    """Return the current union interval without mutating the aggregate row."""
    if not row.team_interval_started_at:
        return 0
    running = list(row.sessions.filter(status=DailySolveTimingSession.STATUS_RUNNING))
    if not running:
        return 0
    fresh_cutoff = now - timedelta(milliseconds=LEASE_STALE_MS)
    effective_end = now if any(
        session.last_heartbeat_at and session.last_heartbeat_at >= fresh_cutoff
        for session in running
    ) else max(
        (session.last_heartbeat_at for session in running if session.last_heartbeat_at),
        default=row.team_interval_started_at,
    )
    return min(
        MAX_ACCUMULATED_MS,
        _ms_between(row.team_interval_started_at, effective_end),
    )


def canonical_elapsed_seconds(
    *,
    game,
    task_group=None,
    user=None,
    anon_key=None,
    attempts=None,
    team=None,
    replay_slot=None,
    timing_row=_UNSET,
) -> int | None:
    """Canonical active seconds, or legacy display fallback outside timing scope."""
    if timing_row is _UNSET:
        tg = task_group
        if tg is None and attempts:
            task = getattr(attempts[0], 'task', None)
            tg = getattr(task, 'task_group', None)
        row = lookup_timing(
            game=game, task_group=tg, team=team, user=user, anon_key=anon_key,
            replay_slot=replay_slot,
        )
    else:
        row = timing_row
    if row is None or int(row.timing_version or 0) < TIMING_VERSION_ACTIVE:
        if team is not None:
            return None
        return elapsed_seconds_from_attempts(attempts)
    return max(0, int(snapshot(row).get('accumulated_ms') or 0) // 1000)


def canonical_elapsed_label(**kwargs) -> str:
    seconds = canonical_elapsed_seconds(**kwargs)
    return '—' if seconds is None else format_elapsed(seconds)


def active_time_ms_for_attempt(*, game, task_group, user=None, anon_key=None, team=None, replay_slot=None, now=None):
    """Return the server-side active clock at an answer submission.

    This deliberately returns ``None`` when no authoritative daily timer exists;
    callers must not turn legacy wall-clock timestamps into active timings.
    """
    row = lookup_timing(
        game=game, task_group=task_group, team=team, user=user, anon_key=anon_key, replay_slot=replay_slot,
    )
    if row is None or int(row.timing_version or 0) < TIMING_VERSION_ACTIVE:
        return None
    snap = snapshot(row, now=now or timezone.now())
    return max(0, int(snap.get('accumulated_ms') or 0))


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
    team = getattr(first, 'team', None) if user is None and not anon_key else None
    tg = None
    if timing_row is _UNSET:
        task = task or getattr(first, 'task', None)
        tg = getattr(task, 'task_group', None)
    return canonical_elapsed_label(
        game=game,
        task_group=tg,
        user=user,
        anon_key=anon_key,
        team=team,
        attempts=attempts,
        timing_row=timing_row,
    )


def _mysql_errno(exc: BaseException) -> int | None:
    current = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        args = getattr(current, 'args', None)
        if args:
            try:
                return int(args[0])
            except (TypeError, ValueError):
                pass
        current = getattr(current, '__cause__', None)
    return None


def _is_mysql_deadlock(exc: BaseException) -> bool:
    return _mysql_errno(exc) == MYSQL_DEADLOCK_ERRNO


def _is_mysql_lock_retryable(exc: BaseException) -> bool:
    return is_mysql_retryable_lock_error(exc)


def record_timing_event(
    *, game, task_group, action, session_id, event_id, seq, claimed_ms=None,
    team=None, user=None, anon_key=None, replay_slot=None, client_occurred_at=None,
):
    """Persist one immutable event before mutating the compatibility snapshot.

    The insert is intentionally a separate short transaction.  It provides a
    durable event ledger without extending the row-lock transaction used by
    the current read-model implementation.  Duplicate delivery is normal for
    browser retries and is treated as success.
    """
    if not event_id or not session_id or action not in MUTATING_ACTIONS:
        return
    actor_key = _timing_event_actor_key(
        team=team, user=user, anon_key=anon_key, replay_slot=replay_slot,
    )
    if not actor_key:
        return
    values = {
        'game': game,
        'task_group': task_group,
        'team': team,
        'user': user,
        'anon_key': str(anon_key) if anon_key else None,
        'replay_slot': replay_slot,
        'session_id': str(session_id)[:128],
        'event_id': str(event_id)[:128],
        'actor_key': actor_key,
        'action': action,
        'seq': int(seq or 0) if str(seq or 0).lstrip('-').isdigit() else 0,
        'claimed_ms': (
            int(claimed_ms)
            if claimed_ms is not None and str(claimed_ms).lstrip('-').isdigit()
            else None
        ),
        'client_occurred_at': client_occurred_at,
    }
    try:
        with transaction.atomic():
            DailyTimingEvent.objects.get_or_create(
                game=game,
                task_group=task_group,
                actor_key=values['actor_key'],
                session_id=values['session_id'],
                event_id=values['event_id'],
                defaults=values,
            )
    except IntegrityError:
        # A concurrent first delivery may have won the unique insert.  Only
        # suppress that specific race; malformed actor data, bad foreign keys,
        # and future constraints must remain visible to callers.
        if DailyTimingEvent.objects.filter(
            game=game,
            task_group=task_group,
            actor_key=values['actor_key'],
            session_id=values['session_id'],
            event_id=values['event_id'],
        ).exists():
            return
        raise


def _timing_event_actor_key(*, team=None, user=None, anon_key=None, replay_slot=None):
    if team is not None:
        prefix = 'team:{}'.format(team.pk)
    elif user is not None:
        prefix = 'user:{}'.format(user.pk)
    elif anon_key:
        prefix = 'anon:{}'.format(str(anon_key))
    else:
        return ''
    if replay_slot is not None:
        prefix += ':replay:{}'.format(replay_slot.pk)
    return prefix[:160]


def apply_timing_event(
    *,
    game,
    task_group,
    user=None,
    anon_key=None,
    team=None,
    action: str,
    session_id,
    event_id: str,
    seq: int,
    claimed_ms=None,
    client_occurred_at=None,
    now=None,
    create: bool = True,
    replay_slot=None,
    timing_request=None,
) -> dict:
    last_exc = None
    action_label = (action or '').strip()
    for attempt in range(1, TIMING_DEADLOCK_ATTEMPTS + 1):
        try:
            result = _apply_timing_event_once(
                game=game,
                task_group=task_group,
                user=user,
                anon_key=anon_key,
                team=team,
                action=action,
                session_id=session_id,
                event_id=event_id,
                seq=seq,
                claimed_ms=claimed_ms,
                now=now,
                create=create,
                replay_slot=replay_slot,
                client_occurred_at=client_occurred_at,
                timing_request=timing_request,
            )
            return result
        except OperationalError as exc:
            last_exc = exc
            if not _is_mysql_lock_retryable(exc):
                raise
            if attempt >= TIMING_DEADLOCK_ATTEMPTS:
                logger.warning(
                    'daily_timing deadlock exhausted attempts=%s action=%s',
                    attempt,
                    action_label,
                )
                raise
            log_lock_retry(
                logger,
                label='daily_timing action={}'.format(action_label),
                attempt=attempt,
                max_attempts=TIMING_DEADLOCK_ATTEMPTS,
                exc=exc,
            )
    raise last_exc


@transaction.atomic
def _apply_timing_event_once(
    *,
    game,
    task_group,
    user=None,
    anon_key=None,
    team=None,
    action: str,
    session_id,
    event_id: str,
    seq: int,
    claimed_ms=None,
    now=None,
    create: bool = True,
    replay_slot=None,
    client_occurred_at=None,
    timing_request=None,
) -> dict:
    now = now or timezone.now()
    action = (action or '').strip()
    if action not in MUTATING_ACTIONS:
        return empty_snapshot()
    filters = actor_filter(team=team, user=user, anon_key=anon_key, replay_slot=replay_slot)
    if filters is None or game is None or task_group is None:
        return empty_snapshot()
    qs = DailySolveTiming.objects.filter(
        game=game,
        task_group=task_group,
        **filters,
    )
    with timing_phase(timing_request, 'timing_row_lock'):
        row = _lock_existing_timing(qs)
    created_timing_row = False
    if row is None:
        if not create or action not in (ACTION_START, ACTION_RESUME):
            return empty_snapshot()
        # A missing timing row cannot itself be locked. Serialize first-row
        # creation on its release so concurrent tabs/team members observe the
        # row created by the winner even on databases where UNIQUE indexes
        # treat NULL replay_slot values as distinct (notably MySQL).
        from games.models import TaskGroup
        with timing_phase(timing_request, 'timing_first_row_lock'):
            TaskGroup.objects.select_for_update().only('pk').get(pk=task_group.pk)
            row = qs.select_for_update().first()
    legacy_elapsed_ms = 0
    if row is None:
        # If the timer endpoint was unavailable during an earlier visit, the
        # player may already have attempts but no DailySolveTiming row. Seed
        # the new active clock from the same legacy elapsed-time formula used
        # by results, then let the current session take over. Returning an
        # empty snapshot here leaves the timer permanently frozen for exactly
        # those players.
        from games.daily.section import is_daily_timing_game
        if is_daily_timing_game(getattr(game, 'id', None)) and _has_prior_statistical_activity(
            game=game, task_group=task_group, actor=filters, replay_slot=replay_slot,
        ):
            from games.models import Attempt
            legacy_attempts = list(Attempt.manager.filter(
                game=game,
                task__task_group=task_group,
                skip=False,
                replay_slot__isnull=True,
                **filters,
            ).order_by('time', 'pk'))
            legacy_elapsed_ms = max(0, int(elapsed_seconds_from_attempts(legacy_attempts) or 0)) * 1000
            logger.info(
                'daily_timing_bootstrap_legacy game=%s task_group=%s elapsed_ms=%s',
                game.pk, task_group.pk, legacy_elapsed_ms,
            )
        create_kwargs = {
            'game': game,
            'task_group': task_group,
            'timing_version': TIMING_VERSION_ACTIVE,
            'status': STATUS_AUTO_PAUSED,
            'accumulated_ms': legacy_elapsed_ms,
            'replay_slot': replay_slot,
        }
        if user is not None:
            create_kwargs['user'] = user
        elif team is not None:
            create_kwargs['team'] = team
            create_kwargs['team_timing_key'] = (
                'first' if replay_slot is None else 'replay:{}'.format(replay_slot.pk)
            )
        else:
            create_kwargs['anon_key'] = str(anon_key)
        try:
            with transaction.atomic():
                row = DailySolveTiming.objects.create(**create_kwargs)
                created_timing_row = True
        except IntegrityError:
            row = _lock_existing_timing(
                DailySolveTiming.objects.filter(
                    game=game, task_group=task_group, **filters,
                )
            )
            if row is None:
                return empty_snapshot()
        else:
            row = DailySolveTiming.objects.select_for_update().get(pk=row.pk)

    # Every actor now uses independent tab/device leases.  The old personal
    # fields are still converted lazily by _team_session() for compatibility.
    _apply_to_team_row(
        row,
        action=action,
        session_id=session_id,
        event_id=event_id,
        seq=seq,
        now=now,
    )
    # Keep the immutable ledger and compatibility snapshot in the same
    # transaction. Failed commands (missing rows) return above and are not
    # recorded; a ledger failure rolls back the snapshot mutation as well.
    record_timing_event(
        game=game,
        task_group=task_group,
        team=team,
        user=user,
        anon_key=anon_key,
        replay_slot=replay_slot,
        action=action,
        session_id=session_id,
        event_id=event_id,
        seq=seq,
        claimed_ms=claimed_ms,
        client_occurred_at=client_occurred_at,
    )
    if created_timing_row and replay_slot is None and getattr(game, 'project_id', None) == 'sections':
        from games.daily_result_projection import schedule_actor_projection
        # Keep the hot timing transaction limited to DailySolveTiming.  The
        # projection state has its own writer (the projection worker); touching
        # it here creates a cross-table lock cycle with a refresh in progress.
        # Register the invalidation only after the timing row commits.
        transaction.on_commit(
            lambda game=game, task_group=task_group, team=team, user=user, anon_key=anon_key:
            schedule_actor_projection(
                game, task_group, team=team, user=user, anon_key=anon_key,
            )
        )
    return snapshot(row, now=now, session_id=session_id)


def complete_daily_timing_in_transaction(
    *, game, task_group, user=None, anon_key=None, team=None,
    replay_slot=None, now=None, timing_phases=None,
) -> dict | None:
    """Freeze an existing timing row inside the caller's transaction.

    Completion owns the transaction boundary.  This primitive deliberately
    does not create a savepoint or retry: callers that need the legacy public
    API should use ``complete_daily_timing`` below.
    """
    now = now or timezone.now()
    filters = actor_filter(team=team, user=user, anon_key=anon_key, replay_slot=replay_slot)
    if filters is None or task_group is None:
        return None
    started = timezone.now()
    row = _lock_existing_timing(
        DailySolveTiming.objects.filter(
            game=game, task_group=task_group, **filters,
        )
    )
    if timing_phases is not None:
        timing_phases['timing_lock_ms'] = (
            timezone.now() - started
        ).total_seconds() * 1000.0
    if row is None:
        return None
    active_session = row.sessions.filter(
        status=DailySolveTimingSession.STATUS_RUNNING,
    ).order_by('pk').first()
    next_seq = max(
        [int(value) for value in row.sessions.values_list('last_seq', flat=True)] or [0],
    ) + 1
    _apply_to_team_row(
        row,
        action=ACTION_COMPLETE,
        session_id=(active_session.session_id if active_session else uuid4()),
        event_id='complete:{}'.format(row.pk),
        seq=next_seq,
        now=now,
    )
    return snapshot(row, now=now)


@transaction.atomic
def complete_daily_timing(*, game, task_group, user=None, anon_key=None, team=None, replay_slot=None, now=None) -> dict | None:
    """Legacy public wrapper for callers outside a completion transaction."""
    return complete_daily_timing_in_transaction(
        game=game,
        task_group=task_group,
        user=user,
        anon_key=anon_key,
        team=team,
        replay_slot=replay_slot,
        now=now,
    )


def merge_timing_rows(target: DailySolveTiming, source: DailySolveTiming) -> DailySolveTiming:
    """Combine two rows for the same daily solve after anon→user or account merge."""
    if target.pk == source.pk:
        return target
    source_sessions = list(source.sessions.all())
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
        target.active_sessions_count = 0
        target.team_interval_started_at = None
        target.sessions.filter(status=DailySolveTimingSession.STATUS_RUNNING).update(
            status=DailySolveTimingSession.STATUS_CLOSED,
            close_reason='completed',
        )
        target.timing_version = max(
            int(target.timing_version or 0),
            int(source.timing_version or 0),
            TIMING_VERSION_ACTIVE,
        )
    else:
        for session in source_sessions:
            if target.sessions.filter(session_id=session.session_id).exists():
                session.delete()
                continue
            session.timing = target
            session.save(update_fields=['timing', 'updated_at'])
        active_sessions = list(target.sessions.filter(
            status=DailySolveTimingSession.STATUS_RUNNING,
        ))
        target.accumulated_ms = max(int(target.accumulated_ms or 0), int(source.accumulated_ms or 0))
        if active_sessions:
            target.status = STATUS_RUNNING
            target.active_sessions_count = len(active_sessions)
            target.team_interval_started_at = min(
                (session.started_at for session in active_sessions if session.started_at),
                default=None,
            )
        elif STATUS_MANUALLY_PAUSED in (target.status, source.status):
            target.status = STATUS_MANUALLY_PAUSED
            target.active_sessions_count = 0
            target.team_interval_started_at = None
        else:
            target.status = STATUS_AUTO_PAUSED
            target.active_sessions_count = 0
            target.team_interval_started_at = None
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


def _team_session(row, sid, *, create=True, now=None):
    sid = _as_uuid(sid)
    if not sid:
        return None
    session = DailySolveTimingSession.objects.select_for_update().filter(
        timing=row, session_id=sid,
    ).first()
    if session is not None or not create:
        return session

    # Rows created before the team-session rollout may still contain one legacy
    # lease. Convert it lazily while the aggregate row is already locked.
    if row.active_session_id and not row.sessions.exists():
        legacy = DailySolveTimingSession.objects.create(
            timing=row,
            session_id=row.active_session_id,
            status=DailySolveTimingSession.STATUS_RUNNING,
            started_at=row.interval_started_at,
            last_heartbeat_at=row.last_heartbeat_at,
        )
        row.active_sessions_count = 1
        row.team_interval_started_at = row.interval_started_at
        row.active_session_id = None
        row.interval_started_at = None
        row.last_heartbeat_at = None
        row.save(update_fields=[
            'active_sessions_count', 'team_interval_started_at',
            'active_session_id', 'interval_started_at', 'last_heartbeat_at',
            'updated_at',
        ])
        if legacy.session_id == sid:
            return legacy

    return DailySolveTimingSession.objects.create(
        timing=row,
        session_id=sid,
        status=DailySolveTimingSession.STATUS_PAUSED,
    )


def _team_remember_event(session, event_id, seq):
    session.last_seq = seq
    if event_id:
        session.last_event_id = event_id
        ids = [item for item in (session.applied_event_ids or []) if item != event_id]
        ids.append(event_id)
        session.applied_event_ids = ids[-APPLIED_EVENT_LIMIT:]


def _team_event_is_stale(session, event_id, seq):
    if event_id and event_id in (session.applied_event_ids or []):
        return True
    return seq <= int(session.last_seq or 0)


def _team_close_aggregate_interval(row, end):
    if row.team_interval_started_at is not None:
        row.accumulated_ms = min(
            MAX_ACCUMULATED_MS,
            int(row.accumulated_ms or 0) + _ms_between(row.team_interval_started_at, end),
        )
    row.team_interval_started_at = None


def _team_expire_sessions(row, now, exclude_session_id=None):
    running = list(row.sessions.select_for_update().filter(
        status=DailySolveTimingSession.STATUS_RUNNING,
    ))
    cutoff = now - timedelta(milliseconds=LEASE_STALE_MS)
    for session in running:
        if exclude_session_id and session.session_id == exclude_session_id:
            continue
        if session.last_heartbeat_at and session.last_heartbeat_at < cutoff:
            session.status = DailySolveTimingSession.STATUS_PAUSED
            session.paused_at = session.last_heartbeat_at
            session.close_reason = 'stale'
            session.save(update_fields=['status', 'paused_at', 'close_reason', 'updated_at'])
    active = [
        session for session in running
        if session.status == DailySolveTimingSession.STATUS_RUNNING
    ]
    row.active_sessions_count = len(active)
    if not active:
        end = max(
            (session.last_heartbeat_at for session in running if session.last_heartbeat_at),
            default=now,
        )
        _team_close_aggregate_interval(row, end)
    return active


def _apply_to_team_row(row, *, action, session_id, event_id, seq, now):
    if row.status == STATUS_COMPLETED:
        return
    sid = _as_uuid(session_id)
    if not sid:
        return
    _team_expire_sessions(
        row,
        now,
        exclude_session_id=sid if action in (ACTION_PAUSE, ACTION_AUTO_PAUSE) else None,
    )
    session = _team_session(row, sid, now=now)
    if session is None:
        return
    try:
        seq = int(seq or 0)
    except (TypeError, ValueError):
        return
    event_id = str(event_id or '').strip()[:64]
    if _team_event_is_stale(session, event_id, seq):
        return

    if action == ACTION_COMPLETE:
        active = list(row.sessions.select_for_update().filter(
            status=DailySolveTimingSession.STATUS_RUNNING,
        ))
        if active:
            fresh_cutoff = now - timedelta(milliseconds=LEASE_STALE_MS)
            end = now if any(
                item.last_heartbeat_at and item.last_heartbeat_at >= fresh_cutoff
                for item in active
            ) else max(
                (item.last_heartbeat_at for item in active if item.last_heartbeat_at),
                default=now,
            )
            _team_close_aggregate_interval(row, end)
        for item in active:
            item.status = DailySolveTimingSession.STATUS_CLOSED
            item.closed_at = now
            item.close_reason = 'completed'
            item.save(update_fields=['status', 'closed_at', 'close_reason', 'updated_at'])
        row.active_sessions_count = 0
        row.status = STATUS_COMPLETED
        row.frozen_ms = min(MAX_ACCUMULATED_MS, max(0, int(row.accumulated_ms or 0)))
        row.accumulated_ms = row.frozen_ms
        row.completed_at = now
        _team_remember_event(session, event_id, seq)
        session.save(update_fields=['last_seq', 'last_event_id', 'applied_event_ids', 'updated_at'])
        row.save()
        return

    if action == ACTION_HEARTBEAT:
        if session.status != DailySolveTimingSession.STATUS_RUNNING:
            return
        session.last_heartbeat_at = now
        _team_remember_event(session, event_id, seq)
        session.save(update_fields=['last_heartbeat_at', 'last_seq', 'last_event_id', 'applied_event_ids', 'updated_at'])
        row.status = STATUS_RUNNING
        row.save(update_fields=['status', 'active_sessions_count', 'team_interval_started_at', 'accumulated_ms', 'updated_at'])
        return

    if action in (ACTION_PAUSE, ACTION_AUTO_PAUSE):
        if session.status != DailySolveTimingSession.STATUS_RUNNING:
            return
        session.status = DailySolveTimingSession.STATUS_PAUSED
        session.paused_at = now
        session.close_reason = 'manual' if action == ACTION_PAUSE else 'auto'
        _team_remember_event(session, event_id, seq)
        session.save(update_fields=[
            'status', 'paused_at', 'close_reason', 'last_seq', 'last_event_id',
            'applied_event_ids', 'updated_at',
        ])
        row.active_sessions_count = max(0, int(row.active_sessions_count or 0) - 1)
        if row.active_sessions_count == 0:
            _team_close_aggregate_interval(row, now)
            row.status = STATUS_MANUALLY_PAUSED if action == ACTION_PAUSE else STATUS_AUTO_PAUSED
        row.save(update_fields=[
            'status', 'active_sessions_count', 'team_interval_started_at',
            'accumulated_ms', 'updated_at',
        ])
        return

    if action in (ACTION_START, ACTION_RESUME):
        if session.status == DailySolveTimingSession.STATUS_RUNNING:
            session.last_heartbeat_at = now
        elif action == ACTION_START and session.close_reason == 'manual':
            return
        else:
            if int(row.active_sessions_count or 0) == 0:
                row.team_interval_started_at = now
            row.active_sessions_count = int(row.active_sessions_count or 0) + 1
            session.status = DailySolveTimingSession.STATUS_RUNNING
            session.started_at = session.started_at or now
            session.last_heartbeat_at = now
            session.paused_at = None
            session.close_reason = ''
        _team_remember_event(session, event_id, seq)
        session.save(update_fields=[
            'status', 'started_at', 'last_heartbeat_at', 'paused_at', 'close_reason',
            'last_seq', 'last_event_id', 'applied_event_ids', 'updated_at',
        ])
        row.status = STATUS_RUNNING
        row.save(update_fields=[
            'status', 'active_sessions_count', 'team_interval_started_at', 'updated_at',
        ])


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
            _close_own_interval(row, sid, claimed, now, cap_ms=MAX_ACCUMULATED_MS)
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
        _close_own_interval(row, sid, claimed, now, cap_ms=MAX_ACCUMULATED_MS)
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
    return min(ms, MAX_ACCUMULATED_MS)


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
    filters = actor_filter(team=team, user=user, anon_key=anon_key)
    if filters is None or not task_group_ids:
        return {}
    rows = DailySolveTiming.objects.filter(
        game=game,
        task_group_id__in=list(task_group_ids),
        **filters,
    )
    return {row.task_group_id: row for row in rows}
