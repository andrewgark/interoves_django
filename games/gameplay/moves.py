"""One gameplay move: who makes it, where, and in which play context."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MoveContext:
    """The resolved actor and play context of a move.

    Exactly one of ``team``, ``user`` and ``anon_key`` is the gameplay actor.
    ``analytics_user`` is the authenticated account behind the request (also
    for team moves); ``run_id`` identifies the replay run when
    ``replay_slot`` is set.
    """

    task: object
    game: object
    team: object = None
    user: object = None
    anon_key: str | None = None
    replay_slot: object = None
    mode: str = 'general'
    analytics_user: object = None
    run_id: object = None

    @property
    def actor(self):
        return {'team': self.team, 'user': self.user, 'anon_key': self.anon_key}
