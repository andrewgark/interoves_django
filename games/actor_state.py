"""Compatibility facade for task actor-state helpers."""

from games.tasks.actor_state import (
    actor_identity_kwargs,
    chain_state_for_actor,
    chain_state_for_result_actor,
    latest_attempt_state_for_result_actor,
)

__all__ = [
    'actor_identity_kwargs',
    'chain_state_for_actor',
    'chain_state_for_result_actor',
    'latest_attempt_state_for_result_actor',
]
