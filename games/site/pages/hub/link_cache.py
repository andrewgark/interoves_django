"""Short-lived, process-local cache of the hub's actor-independent link data.

Every hub view used to rebuild each game's sorted placement list (and the
training cards' newest number) with fresh queries: 11-15 link queries per
view, which dominated the midnight rush. The cached values are the raw lists
only; publication is still filtered against the current time on every
request, so a release at the day boundary needs no invalidation.

Placement edits reach this process through model signals and other processes
within ``HUB_LINK_CACHE_SECONDS``. Cached link objects are shared between
requests and must be treated as read-only.
"""

from __future__ import annotations

import threading
import time

from django.conf import settings
from django.db.models.signals import post_delete, post_save

_lock = threading.Lock()
_entries: dict[tuple, tuple[float, object]] = {}


def _ttl_seconds() -> float:
    return float(getattr(settings, 'HUB_LINK_CACHE_SECONDS', 60))


def cached(key: tuple, build):
    """Return the cached value for ``key`` or build and store it."""
    ttl = _ttl_seconds()
    if ttl <= 0:
        return build()
    now = time.monotonic()
    entry = _entries.get(key)
    if entry is not None and entry[0] > now:
        return entry[1]
    value = build()
    with _lock:
        _entries[key] = (now + ttl, value)
    return value


def clear(*_args, **_kwargs) -> None:
    with _lock:
        _entries.clear()


def game_links(game):
    """``_game_task_group_links(game)`` shared for ``HUB_LINK_CACHE_SECONDS``."""
    from games.site.pages.game.progress import _game_task_group_links

    return cached(('links', str(game.id)), lambda: tuple(_game_task_group_links(game)))


def release_numbers(game):
    """Every placement number of a game (deferred too), shared like the links."""
    from games.models import GameTaskGroup

    return cached(
        ('numbers', str(game.id)),
        lambda: tuple(GameTaskGroup.objects.filter(game=game).values_list('number', flat=True)),
    )


def next_content_transition(games):
    """``next_daily_content_transition_for_games`` over the cached numbers."""
    from django.utils import timezone

    from games.daily.timing.transitions import next_daily_content_transition

    now = timezone.now()
    transitions = [
        next_daily_content_transition(game, release_numbers(game), now=now)
        for game in games if game is not None
    ]
    transitions = [value for value in transitions if value is not None]
    return min(transitions) if transitions else None


def visible_game_links(game):
    """Released placements, newest first (as ``_player_visible_task_group_links``)."""
    from games.daily.page.section import filter_published_links, is_scheduled_game

    links = game_links(game)
    if not is_scheduled_game(game.id):
        return list(links)
    # ``links`` is already in schedule order; only the release filter is
    # time-dependent and therefore evaluated per request.
    return list(reversed(list(filter_published_links(list(links), game))))


def published_numbers(game) -> set:
    from games.daily.page.section import filter_published_links

    return {link.number for link in filter_published_links(list(game_links(game)), game)}


def _connect_invalidation():
    from games.models import Game, GameTaskGroup, Task, TaskGroup

    for model in (Game, GameTaskGroup, TaskGroup, Task):
        uid = 'hub-link-cache-{}'.format(model.__name__)
        post_save.connect(clear, sender=model, dispatch_uid=uid + '-save', weak=False)
        post_delete.connect(clear, sender=model, dispatch_uid=uid + '-delete', weak=False)


_connect_invalidation()
