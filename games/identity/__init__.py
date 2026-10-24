"""Who is playing: team, user, or anonymous key."""

from games.identity.actor import Actor, ActorRefusal, resolve_gameplay_actor

__all__ = ['Actor', 'ActorRefusal', 'resolve_gameplay_actor']
