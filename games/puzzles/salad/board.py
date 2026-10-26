"""One actor's salad screen: chain and attempts. Nothing else."""

from __future__ import annotations

from dataclasses import dataclass

from games.models import Attempt
from games.task_group.actor_state import chain_state_for_actor


@dataclass(frozen=True)
class SaladBoard:
    chain: object
    attempts: list


def load_salad_board(
    *,
    game,
    task,
    team=None,
    user=None,
    anon_key=None,
    mode='general',
    replay_slot=None,
    attempts=None,
):
    """Read the rows the salad board draws for one actor.

    A preloaded attempt list is kept. A missing list is loaded here, still
    limited to this actor, task, game, and replay namespace.
    """
    if team is None and user is None and not anon_key:
        return SaladBoard(None, attempts or [])
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
    return SaladBoard(chain, list(attempts))
