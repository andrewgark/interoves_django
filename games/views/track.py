"""
Live updates over WebSocket (TrackGame) + channel layer.

Roadmap (idiomatic next steps):
- Add typed event names and small payloads where full HTML is not needed.
- user.{id} group for private signals (game start, shipment status).
- Keep group_send inside transaction.on_commit; defer Redis work to a thread so ASGI is not blocked (DEFER_CHANNEL_BROADCAST).
- TrackGame is AsyncJsonWebsocketConsumer; ORM in connect/task_changed uses database_sync_to_async.

Groups:
- track.game.{game_id} — broadcast (e.g. admin changed task text).
- track.game.{game_id}.team.{team_name_hash} — team-scoped task state.
- track.user.{user_id} — private signals (game start, shipment, etc.); same socket as game page.

Messages include monotonic seq per namespace (Django cache) so the client can ignore stale payloads.

Lifecycle: client ping + idle timeout; group_discard wrapped in wait_for so Redis latency
cannot block Daphne application close (prod 504 / "took too long to shut down").
"""
import asyncio
import logging
import threading
import time

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import async_to_sync

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from games.models import Attempt, GameTaskGroup, Task
from games.views.render_task import update_task_html

logger = logging.getLogger(__name__)

_open_track_sockets = 0
_open_track_sockets_lock = threading.Lock()


def _track_ws_open_delta(delta: int) -> int:
    global _open_track_sockets
    with _open_track_sockets_lock:
        _open_track_sockets = max(0, _open_track_sockets + delta)
        return _open_track_sockets


class TrackWsLifecycleMixin:
    """Ping/pong, idle close, and timed channel-layer group_discard."""

    def _track_touch_activity(self):
        self._track_last_activity = time.monotonic()

    async def _track_start_lifecycle(self):
        self._track_touch_activity()
        self._track_idle_task = asyncio.create_task(
            self._track_idle_watch(),
            name='track_ws_idle',
        )
        open_n = _track_ws_open_delta(1)
        every = getattr(settings, 'TRACK_WS_OPEN_LOG_EVERY', 50)
        if open_n == 1 or open_n % every == 0:
            logger.info('Track WebSocket open_count=%s', open_n)

    async def _track_stop_lifecycle(self):
        task = getattr(self, '_track_idle_task', None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._track_idle_task = None
        open_n = _track_ws_open_delta(-1)
        every = getattr(settings, 'TRACK_WS_OPEN_LOG_EVERY', 50)
        if open_n == 0 or open_n % every == 0:
            logger.info('Track WebSocket open_count=%s', open_n)

    async def _track_idle_watch(self):
        timeout = float(getattr(settings, 'TRACK_WS_IDLE_TIMEOUT', 90))
        interval = float(getattr(settings, 'TRACK_WS_IDLE_CHECK_INTERVAL', 15))
        if timeout <= 0:
            return
        interval = max(1.0, interval)
        try:
            while True:
                await asyncio.sleep(interval)
                last = getattr(self, '_track_last_activity', time.monotonic())
                if time.monotonic() - last >= timeout:
                    logger.info('Track WebSocket idle timeout; closing')
                    await self.close(code=4000)
                    return
        except asyncio.CancelledError:
            raise

    async def _track_group_discard(self, group):
        if not group or self.channel_layer is None:
            return
        timeout = float(getattr(settings, 'TRACK_WS_GROUP_DISCARD_TIMEOUT', 2))
        try:
            await asyncio.wait_for(
                self.channel_layer.group_discard(group, self.channel_name),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                'Track group_discard timed out after %ss group=%s',
                timeout,
                group,
            )
        except Exception:
            logger.exception('Track group_discard failed group=%s', group)

    async def receive_json(self, content, **kwargs):
        self._track_touch_activity()
        if isinstance(content, dict) and content.get('type') == 'ping':
            await self.send_json({'type': 'pong'})
            return
        await super().receive_json(content, **kwargs)


def _schedule_channel_broadcast(fn, *, prepare=None):
    """
    Run fn after DB commit. Default: fn runs in a daemon thread so async_to_sync(group_send)
    does not block the Daphne/ASGI loop waiting on Redis (production symptom: POST hangs, :6379 in stack).
    """

    def after_commit():
        # Allocate ordering metadata synchronously in commit-callback order.
        # Redis I/O remains deferred, but a slower older thread can no longer
        # receive a newer seq and overwrite fresher HTML on the client.
        try:
            prepared = prepare() if prepare is not None else None
        except Exception:
            logger.exception('Channel broadcast preparation failed')
            return

        def run_safe():
            try:
                fn(prepared)
            except Exception:
                logger.exception('Channel broadcast failed')

        if getattr(settings, 'DEFER_CHANNEL_BROADCAST', True):
            threading.Thread(
                target=run_safe,
                daemon=True,
                name='interoves_channel_broadcast',
            ).start()
        else:
            run_safe()

    transaction.on_commit(after_commit)


CHANNEL_GROUPS = {
    'game': (lambda game_id: f'track.game.{game_id}'),
    'game_team': (lambda game_id, team_name_hash: f'track.game.{game_id}.team.{team_name_hash}'),
    'user': (lambda user_id: f'track.user.{user_id}'),

    # 'game_results': (lambda game_id: f'track.game.{game_id}.results'),
    # 'total_results': (lambda project_id: f'track.project.{project_id}.total_results'),
    # 'project': (lambda project_id: f'track.project.{project_id}'),
}


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
        return cache.incr(key)
    except ValueError:
        if cache.add(key, 0, timeout=None):
            return cache.incr(key)
        return cache.incr(key)


def envelope_track_message(body: dict, game_id: str) -> dict:
    """Attach seq for game-scoped messages if not already present."""
    out = msgpack_safe_keys(dict(body))
    out.setdefault('seq_namespace', f'game:{game_id}')
    if 'seq' not in out:
        out['seq'] = next_track_seq(out['seq_namespace'])
    return out


def _broadcast_game_track_event_commit(game_id: str, event_name: str, payload: dict):
    """Notify all sockets on track.game.{game_id} (e.g. observers on game page)."""

    namespace = f'game:{game_id}'

    def prepare():
        return next_track_seq(namespace)

    def send(seq):
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        body = envelope_track_message(
            {
                'type': 'track.event',
                'event': event_name,
                'payload': payload,
                'seq': seq,
                'seq_namespace': namespace,
            },
            game_id,
        )
        async_to_sync(channel_layer.group_send)(
            CHANNEL_GROUPS['game'](game_id),
            body,
        )

    _schedule_channel_broadcast(send, prepare=prepare)


def notify_registered_users_game_lifecycle_changed(old_game, new_game):
    """
    When wall-clock phase changes (after Game.save), notify registered teams and
    broadcast to the game group (start/end of window per games.access).

    Uses the same moment as admin UI: game_has_started / game_has_ended at now.
    """
    from games.access import game_has_ended, game_has_started
    from games.models import Registration

    started_before = game_has_started(old_game)
    started_after = game_has_started(new_game)
    ended_before = game_has_ended(old_game)
    ended_after = game_has_ended(new_game)

    events = []
    if not started_before and started_after:
        events.append('game.started')
    if not ended_before and ended_after:
        events.append('game.ended')
    if not events:
        return

    for event_name in events:
        payload = {'game_id': new_game.id}
        _broadcast_game_track_event_commit(new_game.id, event_name, payload)

        notified = set()
        for reg in Registration.objects.filter(game=new_game).select_related('team'):
            team = reg.team
            if team is None:
                continue
            for profile in team.roster_profiles:
                uid = profile.user_id
                if uid in notified:
                    continue
                notified.add(uid)
                notify_user_after_commit(
                    uid,
                    {
                        'type': 'track.event',
                        'event': event_name,
                        'payload': dict(payload),
                    },
                )


def notify_registered_users_play_access_changed(old_game, new_game):
    """
    When a registered team's access to 'play' becomes True, notify each team member
    (e.g. is_ready flipped, times updated). Uses notify_user_after_commit.
    """
    from games.models import Registration

    notified = set()
    for reg in Registration.objects.filter(game=new_game).select_related('team'):
        team = reg.team
        if team is None:
            continue
        if old_game.has_access('play', team=team) or not new_game.has_access('play', team=team):
            continue
        for profile in team.roster_profiles:
            uid = profile.user_id
            if uid in notified:
                continue
            notified.add(uid)
            notify_user_after_commit(
                uid,
                {
                    'type': 'track.event',
                    'event': 'game.play_available',
                    'payload': {'game_id': new_game.id},
                },
            )


def notify_user_after_commit(user_id, body, *, seq_namespace=None):
    """
    Push a message to one user's track socket (TrackGame and/or UserTrackConsumer connected).
    body must include 'type' for the consumer (e.g. type='track.event' -> track_event handler).
    """
    payload = msgpack_safe_keys(dict(body))
    if seq_namespace is None:
        seq_namespace = f'user:{user_id}'
    payload.setdefault('seq_namespace', seq_namespace)

    def prepare():
        if 'seq' in payload:
            return payload['seq']
        return next_track_seq(seq_namespace)

    def send(seq):
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        prepared_payload = dict(payload)
        prepared_payload['seq'] = seq
        async_to_sync(channel_layer.group_send)(
            CHANNEL_GROUPS['user'](user_id),
            prepared_payload,
        )

    _schedule_channel_broadcast(send, prepare=prepare)


def build_event_task_change(
    task,
    team=None,
    current_mode=None,
    update_html=None,
    request=None,
    game=None,
    user=None,
    anon_key=None,
):
    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        by = 'team' if team is not None else ('personal' if user is not None or anon_key else 'admin')
        return {'type': 'task.changed', 'task': task.id, 'by': by}
    if team is not None and current_mode is None:
        attempt = Attempt(task=task, team=team, time=timezone.now())
        current_mode = game.get_current_mode(attempt)

    if request is None and team is not None:
        from django.test.client import RequestFactory
        request = RequestFactory().get(f'/games/{game.id}')
        request.user = team.roster_profiles.first().user

    if update_html is None and request is not None:
        update_html = update_task_html(request, task, team, current_mode, game=game)
    if update_html is None:
        update_html = {}

    by = 'team' if team is not None else ('personal' if user is not None or anon_key else 'admin')
    channel_event = {
        'type': 'task.changed',
        'task': task.id,
        'by': by,
    }
    channel_event.update(update_html)
    return channel_event


def track_task_change(
    task,
    team=None,
    current_mode=None,
    update_html=None,
    request=None,
    game=None,
    user=None,
    anon_key=None,
):
    """
    Notify subscribers after the DB transaction commits so clients never read stale rows.
    Team HTML goes only to the team group; personal HTML goes only to that user.
    Anonymous attempts rely on their POST response, while admin changes go to the game group.
    build_event_task_change runs inside the callback so Task.save() can schedule an update
    before super().save() (the task row exists when the callback runs).
    """
    target_games = []
    if game is not None:
        target_games.append(game)
    else:
        target_games = list(
            GameTaskGroup.objects.filter(task_group_id=task.task_group_id).select_related('game')
        )
        target_games = [x.game for x in target_games]

    def prepare():
        return {
            str(g.id): next_track_seq(f'game:{g.id}')
            for g in target_games
        }

    def send(sequences):
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        for g in target_games:
            event_body = build_event_task_change(
                    task,
                    team,
                    current_mode,
                    update_html,
                    request,
                    game=g,
                    user=user,
                    anon_key=anon_key,
                )
            event_body['seq'] = sequences[str(g.id)]
            event_body['seq_namespace'] = f'game:{g.id}'
            channel_event = envelope_track_message(
                event_body, g.id,
            )
            if team is not None:
                async_to_sync(channel_layer.group_send)(
                    CHANNEL_GROUPS['game_team'](g.id, team.get_name_hash()),
                    channel_event,
                )
            elif user is not None:
                async_to_sync(channel_layer.group_send)(
                    CHANNEL_GROUPS['user'](user.id),
                    channel_event,
                )
            elif anon_key:
                # Анонимные страницы не открывают TrackGame; ответ POST уже
                # содержит HTML для текущей вкладки.
                continue
            else:
                async_to_sync(channel_layer.group_send)(
                    CHANNEL_GROUPS['game'](g.id),
                    channel_event,
                )

    _schedule_channel_broadcast(send, prepare=prepare)


class TrackGame(TrackWsLifecycleMixin, AsyncJsonWebsocketConsumer):
    """Async consumer so group_add/group_send share the same asyncio loop (Channels 4 idiom)."""

    @database_sync_to_async
    def _load_connect_context(self):
        """Profile/team touches the ORM; must not run inside async connect()."""
        user = self.scope['user']
        if not getattr(user, 'is_authenticated', False):
            return None
        profile = getattr(user, 'profile', None)
        team = profile.team_on if profile is not None else None
        team_hash = team.get_name_hash() if team is not None else None
        return (
            user.id,
            team_hash,
            self.scope['url_route']['kwargs']['game_id'],
        )

    async def connect(self):
        ctx = await self._load_connect_context()
        if ctx is None:
            await self.close()
            return
        user_id, team_name_hash, game_id = ctx
        self.user_id = user_id
        self.team_name_hash = team_name_hash
        self.game_id = game_id
        self.group_game = CHANNEL_GROUPS['game'](self.game_id)
        self.group_game_team = (
            CHANNEL_GROUPS['game_team'](self.game_id, self.team_name_hash)
            if self.team_name_hash
            else None
        )
        self.group_user = CHANNEL_GROUPS['user'](user_id)

        await self.accept()

        if self.channel_layer is not None:
            await self.channel_layer.group_add(self.group_game, self.channel_name)
            if self.group_game_team:
                await self.channel_layer.group_add(self.group_game_team, self.channel_name)
            await self.channel_layer.group_add(self.group_user, self.channel_name)
        await self._track_start_lifecycle()

    @database_sync_to_async
    def _build_task_changed_for_admin(self, event):
        user = self.scope['user']
        team = None
        profile = getattr(user, 'profile', None)
        if profile is not None:
            team = profile.team_on
        return build_event_task_change(
            get_object_or_404(Task, id=event['task']),
            team,
        )

    async def task_changed(self, event):
        self._track_touch_activity()
        if event['by'] == 'admin':
            event = await self._build_task_changed_for_admin(event)
        if 'seq' not in event:
            event = envelope_track_message(event, self.game_id)
        else:
            event = msgpack_safe_keys(event)
        await self.send_json(event)

    async def track_event(self, event):
        """User-targeted messages (type='track.event' in group_send body)."""
        self._track_touch_activity()
        event = msgpack_safe_keys(event)
        if 'seq' not in event:
            event = dict(event)
            event['seq'] = next_track_seq(f'user:{self.user_id}')
        await self.send_json(event)

    async def disconnect(self, code):
        await self._track_stop_lifecycle()
        await self._track_group_discard(getattr(self, 'group_game', None))
        await self._track_group_discard(getattr(self, 'group_game_team', None))
        await self._track_group_discard(getattr(self, 'group_user', None))


class UserTrackConsumer(TrackWsLifecycleMixin, AsyncJsonWebsocketConsumer):
    """
    User-only group (track.user.{id}) for hub / pages without a game id in the URL.
    Same track.event payloads as TrackGame.track_event.
    """

    @database_sync_to_async
    def _user_id(self):
        return self.scope['user'].id

    async def connect(self):
        user = self.scope['user']
        if not getattr(user, 'is_authenticated', False):
            await self.close()
            return
        self.user_id = await self._user_id()
        self.group_user = CHANNEL_GROUPS['user'](self.user_id)
        await self.accept()
        if self.channel_layer is not None:
            await self.channel_layer.group_add(self.group_user, self.channel_name)
        await self._track_start_lifecycle()

    async def track_event(self, event):
        self._track_touch_activity()
        if 'seq' not in event:
            event = dict(event)
            event['seq'] = next_track_seq(f'user:{self.user_id}')
        await self.send_json(event)

    async def disconnect(self, code):
        await self._track_stop_lifecycle()
        await self._track_group_discard(getattr(self, 'group_user', None))
