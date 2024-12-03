from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.utils import timezone
from games.check import CheckerFactory
from games.exception import *

from channels.generic.websocket import JsonWebsocketConsumer
from asgiref.sync import async_to_sync


def track_channel_name(game_id, team_name_hash):
    return f'game.track.{game_id}.{team_name_hash}'


class TrackGame(JsonWebsocketConsumer):
    def connect(self):
        self.user_id = self.scope['user'].id
        self.team_name_hash = self.scope['user'].profile.team_on.get_name_hash()
        self.game_id = self.scope['url_route']['kwargs']['game_id']
        self.group = track_channel_name(self.game_id, self.team_name_hash)

        self.accept()

        group_add = async_to_sync(self.channel_layer.group_add)
        group_add(self.group, self.channel_name)

    def task_changed(self, event):
        self.send_json(event)

    def disconnect(self, message):
        group_discard = async_to_sync(self.channel_layer.group_discard)
        group_discard(self.group, self.channel_name)

