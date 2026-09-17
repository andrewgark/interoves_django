"""Private replay slots and the server-side replay context."""

from __future__ import annotations

from uuid import UUID, uuid4

from django.db import transaction
from django.http import Http404
from django.utils import timezone

from games.analytics import game_instance_id_for_task_group
from games.models import (
    DailySolveTiming,
    GameTaskGroup,
    HintAttempt,
    PlayerCompletedGame,
    ReplaySlot,
)

SESSION_KEY_PREFIX = 'interoves_replay:'


class StaleReplayError(Exception):
    """The request belongs to an older replay generation."""


def actor_kwargs(*, team=None, user=None, anon_key=None):
    if team is not None:
        return {'team': team, 'user': None, 'anon_key': None}
    if user is not None:
        return {'team': None, 'user': user, 'anon_key': None}
    if anon_key:
        return {'team': None, 'user': None, 'anon_key': str(anon_key)}
    return None


def replay_actor_key(*, team=None, user=None, anon_key=None):
    """Return a non-null, cross-actor-type uniqueness key for ReplaySlot."""
    if team is not None:
        return 'team:{}'.format(team.pk)
    if user is not None:
        return 'user:{}'.format(user.pk)
    if anon_key:
        return 'anon:{}'.format(anon_key)
    return None


def _session_key(game, task_group):
    return '{}{}:{}'.format(SESSION_KEY_PREFIX, game.pk, task_group.pk)


def _session_value(request, game, task_group):
    session = getattr(request, 'session', None)
    if session is None:
        return None
    value = session.get(_session_key(game, task_group))
    return value if isinstance(value, dict) else None


def active_replay(*, request, game, task_group, team=None, user=None, anon_key=None):
    """Return the active slot, without creating one from a GET or POST."""
    value = _session_value(request, game, task_group)
    actor = actor_kwargs(team=team, user=user, anon_key=anon_key)
    if not value or actor is None:
        return None
    try:
        slot_id = int(value.get('slot_id'))
        run_id = UUID(str(value.get('run_id')))
    except (TypeError, ValueError, AttributeError):
        return None
    slot = ReplaySlot.objects.filter(
        pk=slot_id,
        game=game,
        task_group=task_group,
        run_id=run_id,
        **actor,
    ).first()
    if slot is None:
        return None
    return slot


def bind_replay_session(request, slot):
    request.session[_session_key(slot.game, slot.task_group)] = {
        'slot_id': slot.pk,
        'run_id': str(slot.run_id),
    }
    request.session.modified = True


def clear_replay_session(request, game, task_group):
    request.session.pop(_session_key(game, task_group), None)
    request.session.modified = True


def _official_exists(*, game, task_group, team=None, user=None, anon_key=None):
    actor = actor_kwargs(team=team, user=user, anon_key=anon_key)
    if actor is None:
        return False
    return PlayerCompletedGame.objects.filter(
        game_instance_id=game_instance_id_for_task_group(game, task_group),
        **actor,
    ).exists()


def official_exists_for_attempt(attempt):
    game = attempt.game or (
        GameTaskGroup.resolve_game_for_task(attempt.task) if attempt.task is not None else None
    )
    return (
        attempt.replay_slot_id is None
        and attempt.task is not None
        and game is not None
        and _official_exists(
            game=game,
            task_group=attempt.task.task_group,
            team=attempt.team,
            user=attempt.user,
            anon_key=attempt.anon_key,
        )
    )


@transaction.atomic
def start_or_reset_replay(*, request, game, task_group, team=None, user=None, anon_key=None):
    """Atomically replace the only replay slot and rotate its generation."""
    actor = actor_kwargs(team=team, user=user, anon_key=anon_key)
    if actor is None or not _official_exists(
        game=game, task_group=task_group, team=team, user=user, anon_key=anon_key,
    ):
        raise Http404()

    # The link row is a stable lock for the logical game instance.  It also
    # serialises first creation of a slot before the conditional unique index
    # is consulted by concurrent requests.
    if isinstance(task_group, GameTaskGroup):
        link = task_group
    else:
        link = GameTaskGroup.objects.select_for_update().get(
            game=game, task_group=task_group,
        )
    GameTaskGroup.objects.select_for_update().filter(
        game=game, task_group=task_group,
    ).first()
    slot = ReplaySlot.objects.select_for_update().filter(
        game=game, task_group=task_group,
        actor_key=replay_actor_key(team=team, user=user, anon_key=anon_key),
    ).first()
    if slot is None:
        slot = ReplaySlot.objects.create(
            game=game,
            task_group=task_group,
            run_id=__import__('uuid').uuid4(),
            actor_key=replay_actor_key(team=team, user=user, anon_key=anon_key),
            **actor,
        )
    else:
        # Related rows are all replay-only by construction.
        slot.attempts.all().delete()
        slot.hint_attempts.all().delete()
        slot.chain_task_states.all().delete()
        slot.daily_timings.all().delete()
        slot.run_id = __import__('uuid').uuid4()
        slot.status = 'active'
        slot.updated_at = timezone.now()
        slot.save(update_fields=['run_id', 'status', 'updated_at'])
    bind_replay_session(request, slot)
    return slot


def replay_for_request(*, request, game, task_group, team=None, user=None, anon_key=None):
    """Resolve the session-bound replay and reject a stale gameplay token."""
    slot = active_replay(
        request=request, game=game, task_group=task_group,
        team=team, user=user, anon_key=anon_key,
    )
    token_run = getattr(request, 'interoves_replay_run_id', None)
    token_slot = getattr(request, 'interoves_replay_slot_id', None)
    # A replay form must carry both signed-context values.  A legacy/official
    # tab must never silently fall into the currently active replay session.
    if slot is not None and (not token_run or not token_slot):
        raise StaleReplayError()
    if token_run and (
        slot is None
        or str(slot.run_id) != str(token_run)
        or str(slot.pk) != str(token_slot)
    ):
        raise StaleReplayError()
    return slot


def mark_replay_completed(slot):
    """Persist private lifecycle state without touching official analytics."""
    if slot is None or slot.status == 'completed':
        return
    ReplaySlot.objects.filter(pk=slot.pk, run_id=slot.run_id).update(
        status='completed', updated_at=timezone.now(),
    )


def replay_run_from_request(request):
    return getattr(request, 'interoves_replay_run_id', None)
