"""Per-namespace sequence numbers for live track messages."""
import asyncio
import logging
import weakref

from django.conf import settings
from django.core.cache import caches

from games.realtime.groups import (
    game_track_namespace,
    team_track_namespace,
    user_game_track_namespace,
    user_track_namespace,
)

logger = logging.getLogger('games.views.track')

track_revision_cache = caches['track_revisions']
_track_redis_clients = weakref.WeakKeyDictionary()

def _track_seq_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def current_track_seq(namespace: str) -> int:
    return _track_seq_int(track_revision_cache.get(f'track:seq:{namespace}', 0))


def current_track_versions(game_id=None, *, user_id=None, team_id=None) -> dict:
    namespaces = []
    if game_id is not None:
        namespaces.append(game_track_namespace(game_id))
    if user_id is not None:
        namespaces.append(user_track_namespace(user_id))
        if game_id is not None:
            namespaces.append(user_game_track_namespace(user_id, game_id))
    if game_id is not None and team_id is not None:
        namespaces.append(team_track_namespace(game_id, team_id))
    return {namespace: current_track_seq(namespace) for namespace in namespaces}


def msgpack_safe_keys(value):
    """
    channels_redis msgpack unpack uses strict_map_key=True — int/float dict keys
    pack fine but blow up on receive. Recursively stringify non-str map keys.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, (str, bytes)):
                sk = k
            else:
                sk = str(k)
            out[sk] = msgpack_safe_keys(v)
        return out
    if isinstance(value, list):
        return [msgpack_safe_keys(v) for v in value]
    if isinstance(value, tuple):
        return [msgpack_safe_keys(v) for v in value]
    return value


def next_track_seq(namespace: str) -> int:
    """
    Best-effort monotonic counter (per namespace) using Django cache incr.
    Namespace examples: 'game:mygame_id', 'user:42'.
    """
    key = f'track:seq:{namespace}'
    try:
        return track_revision_cache.incr(key)
    except ValueError:
        if track_revision_cache.add(key, 0, timeout=None):
            return track_revision_cache.incr(key)
        return track_revision_cache.incr(key)


def _async_track_redis_client():
    """Return a loop-local redis.asyncio client for atomic revision updates."""
    from redis.asyncio import Redis

    loop = asyncio.get_running_loop()
    client = _track_redis_clients.get(loop)
    if client is None:
        cache_config = settings.CACHES['track_revisions']
        location = cache_config['LOCATION']
        options = cache_config.get('OPTIONS', {})
        client_options = {
            key: options[key]
            for key in (
                'socket_connect_timeout', 'socket_timeout', 'socket_keepalive',
                'health_check_interval', 'retry_on_timeout', 'ssl_cert_reqs',
            )
            if key in options
        }
        # Keep this auxiliary pool bounded. Each Daphne process may own one
        # pool; a full pool fails fast instead of opening unbounded sockets.
        client_options['max_connections'] = int(options.get('max_connections', 8))
        client = Redis.from_url(location, **client_options)
        _track_redis_clients[loop] = client
    return client


async def next_track_seq_async(namespace: str) -> int:
    """Allocate a scoped sequence without blocking the ASGI event loop.

    Redis INCR creates a missing key and increments it atomically. For local
    development's in-process cache, retain the existing synchronous operation;
    it performs no network I/O and is only used outside production Redis mode.
    """
    from django.core.cache.backends.locmem import LocMemCache

    key = f'track:seq:{namespace}'
    if isinstance(track_revision_cache, LocMemCache):
        return next_track_seq(namespace)
    redis_key = track_revision_cache.make_key(key)
    client = _async_track_redis_client()
    return int(await client.incr(redis_key))


def envelope_track_message(body: dict, game_id: str) -> dict:
    """Attach seq for game-scoped messages if not already present."""
    out = msgpack_safe_keys(dict(body))
    out.setdefault('seq_namespace', f'game:{game_id}')
    if 'seq' not in out:
        out['seq'] = next_track_seq(out['seq_namespace'])
    return out


async def envelope_track_message_async(body: dict, game_id: str) -> dict:
    """Async consumer counterpart; keep the same message fields and key scope."""
    out = msgpack_safe_keys(dict(body))
    out.setdefault('seq_namespace', f'game:{game_id}')
    if 'seq' not in out:
        out['seq'] = await next_track_seq_async(out['seq_namespace'])
    return out
