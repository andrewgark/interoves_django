from channels.layers import get_channel_layer
from channels.generic.websocket import JsonWebsocketConsumer
from asgiref.sync import async_to_sync

from django.shortcuts import get_object_or_404
from django.utils import timezone
from games.models import Attempt, Task, User
from games.views.track.channel_groups import CHANNEL_GROUPS


class TrackProject(JsonWebsocketConsumer):
    def connect(self):
        self.project_id = self.scope['url_route'] #.....
        print('@@@', self.project_id)
        self.group_project = CHANNEL_GROUPS['project'](self.project_id)
        self.accept()
        group_add = async_to_sync(self.channel_layer.group_add)
        group_add(self.group_project, self.channel_name)

    def task_changed(self, event):
        self.send_json(event)

    def disconnect(self, message):
        group_discard = async_to_sync(self.channel_layer.group_discard)
        group_discard(self.group_project, self.channel_name)
