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
"""
import logging
import threading

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import async_to_sync

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from games.models import Attempt, Task
from games.views.render_task import update_task_html

logger = logging.getLogger(__name__)


def _schedule_channel_broadcast(fn):
    """
    Run fn after DB commit. Default: fn runs in a daemon thread so async_to_sync(group_send)
    does not block the Daphne/ASGI loop waiting on Redis (production symptom: POST hangs, :6379 in stack).
    """

    def run_safe():
        try:
            fn()
        except Exception:
            logger.exception('Channel broadcast failed')

    def after_commit():
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
    out = dict(body)
    if 'seq' not in out:
        out['seq'] = next_track_seq(f'game:{game_id}')
    return out


def _broadcast_game_track_event_commit(game_id: str, event_name: str, payload: dict):
    """Notify all sockets on track.game.{game_id} (e.g. observers on game page)."""

    def send():
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        body = envelope_track_message(
            {
                'type': 'track.event',
                'event': event_name,
                'payload': payload,
            },
            game_id,
        )
        async_to_sync(channel_layer.group_send)(
            CHANNEL_GROUPS['game'](game_id),
            body,
        )

    _schedule_channel_broadcast(send)


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
    payload = dict(body)
    if seq_namespace is None:
        seq_namespace = f'user:{user_id}'
    if 'seq' not in payload:
        payload['seq'] = next_track_seq(seq_namespace)

    def send():
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        async_to_sync(channel_layer.group_send)(
            CHANNEL_GROUPS['user'](user_id),
            payload,
        )

    _schedule_channel_broadcast(send)


def build_event_task_change(task, team=None, current_mode=None, update_html=None, request=None):
    game = task.task_group.game
    if team is not None and current_mode is None:
        attempt = Attempt(task=task, team=team, time=timezone.now())
        current_mode = game.get_current_mode(attempt)

    if request is None and team is not None:
        from django.test.client import RequestFactory
        request = RequestFactory().get(f'/games/{game.id}')
        request.user = team.roster_profiles.first().user

    if update_html is None and request is not None:
        update_html = update_task_html(request, task, team, current_mode)
    if update_html is None:
        update_html = {}

    channel_event = {
        'type': 'task.changed',
        'task': task.id,
        'by': 'team' if team is not None else 'admin'
    }
    channel_event.update(update_html)
    return channel_event


def track_task_change(task, team=None, current_mode=None, update_html=None, request=None):
    """
    Notify subscribers after the DB transaction commits so clients never read stale rows.
    build_event_task_change runs inside the callback so Task.save() can schedule an update
    before super().save() (the task row exists when the callback runs).
    """
    game = task.task_group.game

    def send():
        channel_event = envelope_track_message(
            build_event_task_change(task, team, current_mode, update_html, request),
            game.id,
        )
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        if team is not None:
            async_to_sync(channel_layer.group_send)(
                CHANNEL_GROUPS['game_team'](game.id, team.get_name_hash()),
                channel_event,
            )
        else:
            async_to_sync(channel_layer.group_send)(
                CHANNEL_GROUPS['game'](game.id),
                channel_event,
            )

    _schedule_channel_broadcast(send)


class TrackGame(AsyncJsonWebsocketConsumer):
    """Async consumer so group_add/group_send share the same asyncio loop (Channels 4 idiom)."""

    @database_sync_to_async
    def _load_connect_context(self):
        """Profile/team touches the ORM; must not run inside async connect()."""
        user = self.scope['user']
        team = user.profile.team_on
        return (
            user.id,
            team.get_name_hash(),
            self.scope['url_route']['kwargs']['game_id'],
        )

    async def connect(self):
        user_id, team_name_hash, game_id = await self._load_connect_context()
        self.user_id = user_id
        self.team_name_hash = team_name_hash
        self.game_id = game_id
        self.group_game = CHANNEL_GROUPS['game'](self.game_id)
        self.group_game_team = CHANNEL_GROUPS['game_team'](self.game_id, self.team_name_hash)
        self.group_user = CHANNEL_GROUPS['user'](user_id)

        await self.accept()

        if self.channel_layer is not None:
            await self.channel_layer.group_add(self.group_game, self.channel_name)
            await self.channel_layer.group_add(self.group_game_team, self.channel_name)
            await self.channel_layer.group_add(self.group_user, self.channel_name)

    @database_sync_to_async
    def _build_task_changed_for_admin(self, event):
        return build_event_task_change(
            get_object_or_404(Task, id=event['task']),
            self.scope['user'].profile.team_on,
        )

    async def task_changed(self, event):
        if event['by'] == 'admin':
            event = await self._build_task_changed_for_admin(event)
        if 'seq' not in event:
            event = envelope_track_message(event, self.game_id)
        await self.send_json(event)

    async def track_event(self, event):
        """User-targeted messages (type='track.event' in group_send body)."""
        if 'seq' not in event:
            event = dict(event)
            event['seq'] = next_track_seq(f'user:{self.user_id}')
        await self.send_json(event)

    async def disconnect(self, code):
        if self.channel_layer is not None:
            await self.channel_layer.group_discard(self.group_game, self.channel_name)
            await self.channel_layer.group_discard(self.group_game_team, self.channel_name)
            await self.channel_layer.group_discard(self.group_user, self.channel_name)


class UserTrackConsumer(AsyncJsonWebsocketConsumer):
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

    async def track_event(self, event):
        if 'seq' not in event:
            event = dict(event)
            event['seq'] = next_track_seq(f'user:{self.user_id}')
        await self.send_json(event)

    async def disconnect(self, code):
        if self.channel_layer is not None and getattr(self, 'group_user', None):
            await self.channel_layer.group_discard(self.group_user, self.channel_name)
