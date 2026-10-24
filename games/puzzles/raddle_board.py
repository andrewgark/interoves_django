"""One actor's ladder screen: chain, attempts, and draft. Nothing else."""

from __future__ import annotations

from dataclasses import dataclass

from games.models import Attempt
from games.tasks.actor_state import chain_state_for_actor
from games.tasks.result_details import raddle_ui_state_for_actor


@dataclass(frozen=True)
class LadderBoard:
    chain: object
    attempts: list
    hint_attempts: list
    ui_state: object


def load_ladder_board(
    *,
    game,
    task,
    team=None,
    user=None,
    anon_key=None,
    mode='general',
    replay_slot=None,
    attempts=None,
    hint_attempts=None,
):
    """Read the rows the ladder board draws for one actor.

    Preloaded attempt lists are kept. A missing list is loaded here, still
    limited to this actor, task, game, and replay namespace.
    """
    if team is None and user is None and not anon_key:
        return LadderBoard(None, attempts or [], hint_attempts or [], None)
    chain = chain_state_for_actor(
        task,
        game,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
    )
    if attempts is None:
        attempts = Attempt.manager.get_attempts(
            team, task, mode=mode, user=user, anon_key=anon_key,
            game=game, replay_slot=replay_slot,
        )
    if hint_attempts is None:
        hint_attempts = Attempt.manager.get_hint_attempts(
            team, task, mode=mode, user=user, anon_key=anon_key,
            game=game, replay_slot=replay_slot,
        )
    ui_state = raddle_ui_state_for_actor(
        game,
        task,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
    )
    return LadderBoard(chain, list(attempts), list(hint_attempts), ui_state)
