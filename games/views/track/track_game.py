from channels.layers import get_channel_layer
from channels.generic.websocket import JsonWebsocketConsumer
from asgiref.sync import async_to_sync

from django.shortcuts import get_object_or_404
from django.utils import timezone
from games.models import Attempt, Task, User
from games.views.render_task import update_task_html
from games.views.track.channel_groups import CHANNEL_GROUPS


def build_event_task_change(task, request=None, user=None, team=None, current_mode=None, update_html=None):
    game = task.task_group.game
    if request is not None:
        user = request.user
    if user is not None and team is None:
        team = user.profile.team_on

    if team is not None and current_mode is None:
        attempt = Attempt(task=task, team=team, time=timezone.now())
        current_mode = game.get_current_mode(attempt)

    if request is None and user is not None:
        from django.test.client import RequestFactory
        request = RequestFactory().get(f'/games/{game.id}')
        request.user = user

    if update_html is None and request is not None:
        update_html = update_task_html(request=request, task=task, team=team, current_mode=current_mode)
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
    game = task.task_group.game
    channel_event = build_event_task_change(task, team, current_mode, update_html, request)
    channel_layer = get_channel_layer()
    if team is not None:
        async_to_sync(channel_layer.group_send)(
            CHANNEL_GROUPS['game_team'](game.id, team.get_name_hash()),
            channel_event
        )
    else:
        print('%', CHANNEL_GROUPS['game'](game.id), channel_event)
        async_to_sync(channel_layer.group_send)(
            CHANNEL_GROUPS['game'](game.id),
            channel_event
        )


class TrackGame(JsonWebsocketConsumer):
    def connect(self):
        self.team_name_hash = self.scope['user'].profile.team_on.get_name_hash()
        self.game_id = self.scope['url_route']['kwargs']['game_id']
        self.group_game = CHANNEL_GROUPS['game'](self.game_id)
        self.group_game_team = CHANNEL_GROUPS['game_team'](self.game_id, self.team_name_hash)

        self.accept()

        group_add = async_to_sync(self.channel_layer.group_add)
        group_add(self.group_game, self.channel_name)
        group_add(self.group_game_team, self.channel_name)

    def task_changed(self, event):
        if event['by'] == 'admin':
            event = build_event_task_change(
                task=get_object_or_404(Task, id=event['task']),
                user=self.scope['user'],
            )
        self.send_json(event)

    def disconnect(self, message):
        group_discard = async_to_sync(self.channel_layer.group_discard)
        group_discard(self.group_game, self.channel_name)
        group_discard(self.group_game_team, self.channel_name)
