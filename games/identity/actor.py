"""Gameplay actor for a write: one team, one user, or one anonymous key.

Page hubs and the timing worker resolve a player differently. Those policies
stay next to their callers until they are shown to be the same rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone

from games.ops.analytics.identity import gameplay_anon_key
from games.site.http.exceptions import NoGameAccessException
from games.identity.profile import has_profile, has_team


@dataclass(frozen=True)
class Actor:
    """The player a gameplay write is stored against."""

    play_mode: str
    team: object = None
    user: object = None
    anon_key: str | None = None

    def __post_init__(self):
        if self.play_mode not in ('team', 'personal'):
            raise ValueError('play_mode must be team or personal')
        present = (
            self.team is not None,
            self.user is not None,
            bool(self.anon_key),
        )
        if sum(present) != 1:
            raise ValueError('Actor needs exactly one of team, user, anon_key')
        if self.play_mode == 'team' and self.team is None:
            raise ValueError('team play_mode requires a team')
        if self.play_mode == 'personal' and self.team is not None:
            raise ValueError('personal play_mode cannot carry a team')

    @property
    def kind(self) -> str:
        if self.team is not None:
            return 'team'
        if self.user is not None:
            return 'user'
        return 'anon'


@dataclass(frozen=True)
class ActorRefusal:
    """The request has no gameplay actor. ``status`` is the existing JSON code."""

    status: str


def resolve_gameplay_actor(request, game, play_mode, *, check_access=True):
    """Resolve the actor for send-attempt, hint, and ladder writes.

    ``play_mode`` is already chosen by the caller (header, then session, then
    ``effective_play_mode``). This function does not read the session again.
    """
    if play_mode == 'team':
        if not request.user.is_authenticated or not has_team(request.user):
            return ActorRefusal('no_team')
        team = request.user.profile.team_on
        if check_access and not game.has_access('send_attempt', team=team):
            raise NoGameAccessException(
                'User has no access to game {}'.format(game),
            )
        return Actor(play_mode='team', team=team)

    if request.user.is_authenticated:
        if not has_profile(request.user):
            return ActorRefusal('no_profile')
        user = request.user
        anon_key = None
    else:
        user = None
        anon_key = gameplay_anon_key(request)
        if not anon_key:
            return ActorRefusal('no_anon')

    if check_access and not _personal_access(game):
        raise NoGameAccessException(
            'User has no access to game {}'.format(game),
        )
    return Actor(play_mode='personal', user=user, anon_key=anon_key)


def _personal_access(game) -> bool:
    from games.models import Attempt

    return game.has_access(
        'read_googledoc',
        team=None,
        attempt=Attempt(time=timezone.now()),
    )
