from channels.layers import get_channel_layer
from channels.generic.websocket import JsonWebsocketConsumer
from asgiref.sync import async_to_sync

from django.shortcuts import get_object_or_404
from django.utils import timezone
from games.models import Attempt, Task, User
from games.views.track.channel_groups import get_channel_group


class TrackProject(JsonWebsocketConsumer):
    def connect(self):
        self.project_id = self.scope['url_route']['kwargs']['project_id']
        self.group_project = get_channel_group('project', self.project_id)
        self.accept()
        if self.group_project is not None:
            group_add = async_to_sync(self.channel_layer.group_add)
            group_add(self.group_project, self.channel_name)

    def game_changed(self, event):
        self.send_json(event)

    def disconnect(self, message):
        group_discard = async_to_sync(self.channel_layer.group_discard)
        if self.group_project is not None:
            group_discard(self.group_project, self.channel_name)
