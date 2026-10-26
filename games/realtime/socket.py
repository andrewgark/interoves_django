"""WebSocket consumers for live track updates."""
import asyncio
import logging
import socket
import threading
import time
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.shortcuts import get_object_or_404

from games.site.http.auth_observability import log_realtime_sync
from games.models import Game, Profile, Task
from games.realtime.broadcast import build_event_task_change, live_page_actor_for_mode
from games.realtime.counter import (
    _track_seq_int,
    current_track_versions,
    envelope_track_message_async,
    msgpack_safe_keys,
    next_track_seq_async,
)
from games.realtime.groups import CHANNEL_GROUPS, user_track_namespace

logger = logging.getLogger('games.views.track')

_open_track_sockets = 0
_open_track_sockets_lock = threading.Lock()

def _track_ws_open_delta(delta: int) -> int:
    global _open_track_sockets
    with _open_track_sockets_lock:
        _open_track_sockets = max(0, _open_track_sockets + delta)
        return _open_track_sockets


class TrackWsLifecycleMixin:
    """Ping/pong, idle close, and timed channel-layer group_discard."""

    async def _ensure_current_access(self):
        # The user-only hub has no game authorization to recheck.
        return True

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
            logger.info(
                'websocket_connected host=%s active_count=%s',
                socket.gethostname(), open_n,
            )

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
            logger.info(
                'websocket_disconnected host=%s active_count=%s',
                socket.gethostname(), open_n,
            )

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
            if not await self._ensure_current_access():
                return
            await self.send_json({'type': 'pong'})
            return
        if isinstance(content, dict) and content.get('type') == 'track.sync':
            if not await self._ensure_current_access():
                return
            seen = content.get('seen')
            if not isinstance(seen, dict):
                seen = {}
            versions = await self._track_current_versions()
            missed = {
                namespace: current
                for namespace, current in versions.items()
                if namespace in seen and _track_seq_int(seen.get(namespace)) < current
            }
            reset = {
                namespace: current
                for namespace, current in versions.items()
                if namespace in seen and _track_seq_int(seen.get(namespace)) > current
            }
            log_realtime_sync(
                'reconcile_request',
                game_id=getattr(self, 'game_id', None),
                user_id=getattr(self, 'user_id', None),
                team_id=getattr(self, 'team_id', None),
                delivery='websocket',
                missed_namespaces=list(missed),
                reset_namespaces=list(reset),
            )
            await self.send_json({
                'type': 'track.resync_required' if missed or reset else 'track.synced',
                'versions': versions,
                'missed': missed,
                'reset': reset,
            })
            return
        await super().receive_json(content, **kwargs)

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
        kwargs = self.scope['url_route']['kwargs']
        game_id = kwargs['game_id']
        games = Game.objects.filter(pk=game_id)
        project_id = kwargs.get('project_id')
        if project_id:
            games = games.filter(project_id=project_id)
        game = games.first()
        if game is None or not game.has_access('see_game_preview', team=team):
            return None
        query = parse_qs(
            (self.scope.get('query_string') or b'').decode('ascii', errors='ignore'),
        )
        requested_mode = (query.get('play_mode') or [None])[0]
        team_actor, _actor_user, play_mode = live_page_actor_for_mode(
            user,
            self.scope.get('session'),
            game,
            requested_mode=requested_mode,
        )
        # A personal page must not subscribe to its account's team stream.
        # Keep the user stream for private lifecycle events in either mode.
        if team is not None and (team_actor is None or team_actor.pk != team.pk):
            team = None
        team_hash = team.get_name_hash() if team is not None else None
        return (
            user.id,
            team.pk if team is not None else None,
            team_hash,
            game_id,
            play_mode,
        )

    async def connect(self):
        ctx = await self._load_connect_context()
        if ctx is None:
            await self.close()
            return
        user_id, team_id, team_name_hash, game_id, play_mode = ctx
        self.user_id = user_id
        self.team_id = team_id
        self.team_name_hash = team_name_hash
        self.game_id = game_id
        self.play_mode = play_mode
        self.group_game = CHANNEL_GROUPS['game'](self.game_id)
        self.group_game_team = (
            CHANNEL_GROUPS['game_team'](self.game_id, self.team_id)
            if self.team_id is not None
            else None
        )
        self.group_game_team_legacy = (
            CHANNEL_GROUPS['game_team_legacy'](self.game_id, self.team_name_hash)
            if self.team_name_hash
            else None
        )
        self.group_user = CHANNEL_GROUPS['user'](user_id)
        self.group_user_game = CHANNEL_GROUPS['user_game'](user_id, game_id)

        await self.accept()

        if self.channel_layer is not None:
            await self.channel_layer.group_add(self.group_game, self.channel_name)
            if self.group_game_team:
                await self.channel_layer.group_add(self.group_game_team, self.channel_name)
            if self.group_game_team_legacy:
                await self.channel_layer.group_add(
                    self.group_game_team_legacy, self.channel_name,
                )
            await self.channel_layer.group_add(self.group_user, self.channel_name)
            await self.channel_layer.group_add(self.group_user_game, self.channel_name)
        await self._track_start_lifecycle()

    @database_sync_to_async
    def _track_current_versions(self):
        return current_track_versions(
            self.game_id,
            user_id=self.user_id,
            team_id=self.team_id,
        )

    @database_sync_to_async
    def _has_current_access(self):
        """Recheck game access and the actor represented by this socket."""
        user = self.scope['user']
        if not getattr(user, 'is_authenticated', False):
            return False
        game = Game.objects.filter(pk=self.game_id).first()
        if game is None:
            return False
        # The ASGI scope's User instance can retain a cached Profile from
        # connect(); fetch it fresh so a team removal is visible on this socket.
        profile = Profile.objects.filter(user_id=user.pk).select_related('team_on').first()
        if profile is None:
            return False
        user.profile = profile
        current_team = profile.team_on if profile is not None else None
        if not game.has_access('see_game_preview', team=current_team):
            return False
        selected_team, _actor_user, _mode = live_page_actor_for_mode(
            user,
            self.scope.get('session'),
            game,
            requested_mode=self.play_mode,
        )
        selected_team_id = selected_team.pk if selected_team is not None else None
        return selected_team_id == self.team_id

    async def _ensure_current_access(self):
        if await self._has_current_access():
            return True
        logger.info(
            'track_websocket_access_revoked game_id=%s user_id=%s team_id=%s',
            getattr(self, 'game_id', None),
            getattr(self, 'user_id', None),
            getattr(self, 'team_id', None),
        )
        await self.close(code=4403)
        return False

    @database_sync_to_async
    def _build_task_changed_for_admin(self, event):
        user = self.scope['user']
        game = get_object_or_404(Game, id=self.game_id)
        team, actor_user, _mode = live_page_actor_for_mode(
            user, self.scope.get('session'), game, requested_mode=self.play_mode,
        )
        return build_event_task_change(
            get_object_or_404(Task, id=event['task']),
            team,
            user=actor_user,
            game=game,
        )

    async def task_changed(self, event):
        if not await self._ensure_current_access():
            return
        self._track_touch_activity()
        log_realtime_sync(
            'deliver', game_id=getattr(self, 'game_id', None),
            task_id=event.get('task'), recipient_user_id=getattr(self, 'user_id', None),
            reason=event.get('reason'), seq=event.get('seq'),
            seq_namespace=event.get('seq_namespace'), delivery='websocket',
        )
        if event['by'] == 'admin':
            event = await self._build_task_changed_for_admin(event)
        if 'seq' not in event:
            event = await envelope_track_message_async(event, self.game_id)
        else:
            event = msgpack_safe_keys(event)
        await self.send_json(event)

    async def track_event(self, event):
        """User-targeted messages (type='track.event' in group_send body)."""
        self._track_touch_activity()
        log_realtime_sync(
            'deliver', game_id=getattr(self, 'game_id', None),
            recipient_user_id=getattr(self, 'user_id', None),
            reason=event.get('event'), seq=event.get('seq'),
            seq_namespace=event.get('seq_namespace'), delivery='websocket',
        )
        event = msgpack_safe_keys(event)
        if 'seq' not in event:
            event = dict(event)
            event['seq'] = await next_track_seq_async(f'user:{self.user_id}')
        await self.send_json(event)

    async def disconnect(self, code):
        await self._track_stop_lifecycle()
        await self._track_group_discard(getattr(self, 'group_game', None))
        await self._track_group_discard(getattr(self, 'group_game_team', None))
        await self._track_group_discard(getattr(self, 'group_game_team_legacy', None))
        await self._track_group_discard(getattr(self, 'group_user', None))
        await self._track_group_discard(getattr(self, 'group_user_game', None))


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

    @database_sync_to_async
    def _track_current_versions(self):
        return current_track_versions(user_id=self.user_id)

    async def track_event(self, event):
        self._track_touch_activity()
        if 'seq' not in event:
            event = dict(event)
            event['seq'] = await next_track_seq_async(f'user:{self.user_id}')
        await self.send_json(event)

    async def task_changed(self, event):
        """Personal task updates may share the user group with a hub socket."""
        self._track_touch_activity()
        log_realtime_sync(
            'deliver', task_id=event.get('task'),
            recipient_user_id=getattr(self, 'user_id', None),
            reason=event.get('reason'), seq=event.get('seq'),
            seq_namespace=event.get('seq_namespace'), delivery='websocket',
        )
        if 'seq' not in event:
            event = dict(event)
            event['seq_namespace'] = user_track_namespace(self.user_id)
            event['seq'] = await next_track_seq_async(event['seq_namespace'])
        await self.send_json(msgpack_safe_keys(event))

    async def disconnect(self, code):
        await self._track_stop_lifecycle()
        await self._track_group_discard(getattr(self, 'group_user', None))
