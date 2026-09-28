"""Compatibility facade for results access helpers."""

from games.results.access import (
    anon_key_from_request,
    public_exclusion_notice,
    results_actor_for_request,
    results_me_participants,
)

__all__ = [
    'anon_key_from_request',
    'public_exclusion_notice',
    'results_actor_for_request',
    'results_me_participants',
]
