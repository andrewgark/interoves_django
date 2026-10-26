"""Who is playing: team, user, or anonymous key."""

from games.identity.actor import Actor, ActorRefusal, resolve_gameplay_actor
from games.identity.profile import has_profile, has_team

__all__ = [
    'Actor',
    'ActorRefusal',
    'resolve_gameplay_actor',
    'has_profile',
    'has_team',
]
