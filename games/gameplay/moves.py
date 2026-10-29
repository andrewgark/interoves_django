"""One gameplay move: who makes it, where, and what it changes.

``submit_move`` is the request-independent part of /send_attempt/: the view
resolves the actor and reads the POST into a ``MoveInput``; the offline sync
endpoint will build the same input from a queued event.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass

from django.utils import timezone

from games.gameplay.attempts import check_attempt
from games.gameplay.client_events import receipt_attempt
from games.gameplay.effects import MoveEffects, apply_move_effects
from games.gameplay.hints import create_hint_attempt, get_first_new_hint_actor
from games.models import CHAIN_TASK_TYPES, Attempt
from games.ops.analytics.events import is_task_completion_state
from games.puzzles import get_puzzle
from games.site.http.auth_observability import log_gameplay_attempt_created
from games.site.http.exceptions import ReplayedClientEventException
from games.site.http.middleware.request_timing import timing_phase


@dataclass(frozen=True)
class MoveContext:
    """The resolved actor and play context of a move.

    Exactly one of ``team``, ``user`` and ``anon_key`` is the gameplay actor.
    ``analytics_user`` is the authenticated account behind the request (also
    for team moves); ``run_id`` identifies the replay run when
    ``replay_slot`` is set. ``mode`` is None until ``submit_move`` derives it
    from the time of the move.
    """

    task: object
    game: object
    team: object = None
    user: object = None
    anon_key: str | None = None
    replay_slot: object = None
    mode: str | None = None
    analytics_user: object = None
    run_id: object = None

    @property
    def actor(self):
        return {'team': self.team, 'user': self.user, 'anon_key': self.anon_key}


@dataclass(frozen=True)
class MoveInput:
    """What the player sent: the attempt text and how to treat it.

    ``word_index``/``word`` are set for one-word moves (the ladder);
    ``starts_game`` marks a move that starts the game even if nothing is
    persisted; ``persist_wrong`` False keeps a wrong probe out of the attempts.
    """

    text: str
    word_index: int | None = None
    word: str | None = None
    starts_game: bool = False
    persist_wrong: bool = True
    client_event_id: uuid.UUID | None = None


@dataclass
class MoveOutcome:
    """How ``submit_move`` ended.

    kind:
      ``checked``         the move was checked; ``persisted`` says if it was saved;
      ``replayed``        ``attempt`` is what this client event already produced;
      ``stale_word``      the ladder word is no longer playable; ``stale`` holds
                          the sync fields and nothing was saved;
      ``replay_required`` the official game is complete and this is not a
                          repeat of an earlier answer.
    """

    kind: str
    ctx: MoveContext
    attempt: Attempt | None = None
    persisted: bool = False
    effects: MoveEffects = dataclasses.field(default_factory=MoveEffects)
    stale: dict | None = None


def submit_move(ctx, move, *, submitter_profile=None, observer=None):
    """Check and persist one move of ``ctx``'s actor and apply what follows it.

    ``submitter_profile`` is the authenticated member who asked (for hints the
    move opens); ``observer`` is the request that carries the move: its user
    is recorded as the submitter, and it receives timing phases and the
    attempt-created log.
    """
    task, game = ctx.task, ctx.game
    attempt = Attempt(
        text=move.text,
        team=ctx.team,
        user=ctx.user,
        anon_key=ctx.anon_key,
        task=task,
        time=timezone.now(),
        game=game,
        replay_slot=ctx.replay_slot,
    )

    # A resent move answers with the attempt it already produced, before any
    # check that the first delivery itself may have changed (e.g. a ladder
    # word that is no longer playable because it was just solved).
    if move.client_event_id is not None:
        replayed = receipt_attempt(move.client_event_id, attempt)
        if replayed is not None:
            return _replayed(ctx, replayed)

    if ctx.replay_slot is None and _needs_replay(ctx, attempt.text):
        return MoveOutcome('replay_required', ctx)

    ctx = dataclasses.replace(ctx, mode=game.get_current_mode(attempt))

    puzzle = get_puzzle(task.task_type)
    stale_for = getattr(puzzle, 'stale_payload', None) if puzzle is not None else None
    if stale_for is not None and move.word_index is not None:
        stale = stale_for(
            task, ctx.team, ctx.user, ctx.anon_key, game, ctx.mode, move.word_index, ctx.replay_slot,
        )
        if stale is not None:
            return MoveOutcome('stale_word', ctx, stale=stale)

    with timing_phase(observer, 'check_attempt'):
        try:
            persisted = check_attempt(
                attempt,
                persist_wrong=move.persist_wrong,
                timing_request=observer,
                client_event_id=move.client_event_id,
            )
        except ReplayedClientEventException as exc:
            return _replayed(ctx, Attempt.manager.get(pk=exc.attempt_id))
    if persisted and observer is not None:
        observer.interoves_attempt_id = attempt.pk
        with timing_phase(observer, 'attempt_created_log'):
            log_gameplay_attempt_created(
                observer,
                attempt=attempt,
                actor_kind=observer.interoves_gameplay_actor_kind,
            )

    if persisted and task.task_type == 'autohint':
        _open_autohint(ctx, attempt, submitter_profile=submitter_profile, observer=observer)

    effects = apply_move_effects(
        ctx,
        started=bool(persisted or move.starts_game),
        may_complete=bool(persisted) and (
            attempt.status == 'Ok'
            or task.task_type in CHAIN_TASK_TYPES
            or is_task_completion_state(task, attempt.state)
        ),
        source='send_attempt',
        observer=observer,
    )
    return MoveOutcome('checked', ctx, attempt=attempt, persisted=bool(persisted), effects=effects)


def _replayed(ctx, attempt):
    return MoveOutcome(
        'replayed',
        dataclasses.replace(ctx, mode=ctx.game.get_current_mode(attempt)),
        attempt=attempt,
        persisted=True,
    )


REPLAY_REQUIRED = {'status': 'error', 'error': 'replay_required', 'reload_required': True}


def official_game_completed(ctx):
    """The actor already completed the official game; new moves need a replay."""
    from games.site.session.replay import _official_exists

    return _official_exists(game=ctx.game, task_group=ctx.task.task_group, **ctx.actor)


def _needs_replay(ctx, text):
    """After the official completion only an earlier answer may be sent again."""
    if not official_game_completed(ctx):
        return False
    return not Attempt.manager.filter(
        task=ctx.task,
        game=ctx.game,
        **ctx.actor,
        replay_slot=None,
        text=text,
    ).exists()


def _open_autohint(ctx, attempt, *, submitter_profile, observer):
    from games.puzzles.markers.autohint import AutohintPuzzle

    if not AutohintPuzzle.opens_on_wrong(attempt):
        return
    with timing_phase(observer, 'autohint'):
        hint = get_first_new_hint_actor(ctx.task, **ctx.actor, replay_slot=ctx.replay_slot)
        if hint is not None:
            create_hint_attempt(
                hint, **ctx.actor,
                game=ctx.game, replay_slot=ctx.replay_slot,
                requested_by_profile=submitter_profile,
            )
