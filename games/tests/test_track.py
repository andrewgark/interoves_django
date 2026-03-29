"""
Tests for WebSocket track groups and deferred channel sends (on_commit).

Integration tests use WebsocketCommunicator + session cookie; TrackGame is async
so InMemoryChannelLayer groups line up with the test event loop.
"""
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from unittest.mock import AsyncMock, MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from datetime import timedelta

from games.models import (
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    Registration,
    Task,
    TaskGroup,
    Team,
)
from games.views.track import (
    CHANNEL_GROUPS,
    build_event_task_change,
    next_track_seq,
    notify_registered_users_game_lifecycle_changed,
    notify_registered_users_play_access_changed,
    notify_user_after_commit,
    track_task_change,
)


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')


class TrackGameFixtureMixin:
    """Shared game / task / team / user for track tests (avoid subclass duplication)."""

    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.game, _ = Game.objects.get_or_create(
            pk='test_game_track',
            defaults={
                'name': 'Track test',
                'author': 'test',
                'author_extra': '',
            },
        )
        cls.task_group = TaskGroup.objects.create(label='tg')
        GameTaskGroup.objects.create(
            game=cls.game, task_group=cls.task_group, number=1, name='tg',
        )
        with patch('games.views.track.track_task_change'):
            cls.task = Task.objects.create(
                task_group=cls.task_group,
                number='1',
                task_type='default',
            )
        cls.team = Team.objects.create(name='team_track_tests', visible_name='Track tests')
        cls.user = User.objects.create_user('track_ws_user', 'track_ws_user@example.com', 'pw')
        Profile.objects.create(
            user=cls.user,
            first_name='T',
            last_name='U',
            team_on=cls.team,
        )


@override_settings(DEFER_CHANNEL_BROADCAST=False)
class TrackChannelTests(TrackGameFixtureMixin, TestCase):
    """Синхронная отправка в Channels, чтобы captureOnCommitCallbacks видел group_send сразу."""

    def test_channel_group_names(self):
        self.assertEqual(CHANNEL_GROUPS['game']('g1'), 'track.game.g1')
        self.assertEqual(
            CHANNEL_GROUPS['game_team']('g1', 'abc'),
            'track.game.g1.team.abc',
        )
        self.assertEqual(CHANNEL_GROUPS['user'](42), 'track.user.42')

    def test_next_track_seq_monotonic(self):
        from django.core.cache import cache

        ns = 'unit_test_seq_namespace'
        cache.delete(f'track:seq:{ns}')
        self.assertEqual(next_track_seq(ns), 1)
        self.assertEqual(next_track_seq(ns), 2)

    def test_build_event_task_change_team_with_explicit_html(self):
        event = build_event_task_change(
            self.task,
            team=self.team,
            current_mode='general',
            update_html={'extra': 'x'},
        )
        self.assertEqual(event['type'], 'task.changed')
        self.assertEqual(event['task'], self.task.id)
        self.assertEqual(event['by'], 'team')
        self.assertEqual(event['extra'], 'x')

    def test_build_event_task_change_admin(self):
        event = build_event_task_change(
            self.task,
            team=None,
            update_html={'k': 'v'},
        )
        self.assertEqual(event['by'], 'admin')
        self.assertEqual(event['k'], 'v')

    @override_settings(
        CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
    )
    def test_track_task_change_sends_to_team_group_after_commit(self):
        layer = MagicMock()
        layer.group_send = AsyncMock(return_value=None)
        with patch('games.views.track.get_channel_layer', return_value=layer):
            with self.captureOnCommitCallbacks(execute=True):
                track_task_change(
                    self.task,
                    team=self.team,
                    update_html={'stub': True},
                )
        layer.group_send.assert_called_once()
        args, _kwargs = layer.group_send.call_args
        expected_group = CHANNEL_GROUPS['game_team'](self.game.id, self.team.get_name_hash())
        self.assertEqual(args[0], expected_group)
        self.assertEqual(args[1]['type'], 'task.changed')
        self.assertEqual(args[1]['stub'], True)
        self.assertIn('seq', args[1])
        self.assertIsInstance(args[1]['seq'], int)

    @override_settings(
        CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
    )
    def test_track_task_change_sends_to_game_group_for_admin(self):
        layer = MagicMock()
        layer.group_send = AsyncMock(return_value=None)
        with patch('games.views.track.get_channel_layer', return_value=layer):
            with self.captureOnCommitCallbacks(execute=True):
                track_task_change(self.task, update_html={'admin': 1})
        layer.group_send.assert_called_once()
        args, _kwargs = layer.group_send.call_args
        self.assertEqual(args[0], CHANNEL_GROUPS['game'](self.game.id))
        self.assertEqual(args[1]['by'], 'admin')
        self.assertIn('seq', args[1])

    @override_settings(
        CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
    )
    def test_notify_user_after_commit_targets_user_group(self):
        layer = MagicMock()
        layer.group_send = AsyncMock(return_value=None)
        with patch('games.views.track.get_channel_layer', return_value=layer):
            with self.captureOnCommitCallbacks(execute=True):
                notify_user_after_commit(
                    self.user.id,
                    {
                        'type': 'track.event',
                        'event': 'test.ping',
                    },
                )
        layer.group_send.assert_called_once()
        args, _kwargs = layer.group_send.call_args
        self.assertEqual(args[0], CHANNEL_GROUPS['user'](self.user.id))
        self.assertEqual(args[1]['type'], 'track.event')
        self.assertEqual(args[1]['event'], 'test.ping')
        self.assertIn('seq', args[1])

    def test_notify_registered_users_play_access_changed_calls_user_notify(self):
        self.game.is_tournament = True
        self.game.is_ready = False
        self.game.is_registrable = True
        self.game.is_playable = True
        self.game.start_time = timezone.now() - timedelta(hours=1)
        self.game.end_time = timezone.now() + timedelta(hours=1)
        self.game.save()
        Registration.objects.create(game=self.game, team=self.team)
        with patch('games.views.track.notify_user_after_commit') as m:
            self.game.is_ready = True
            self.game.save()
        self.assertEqual(m.call_count, 1)
        m.reset_mock()
        self.game.save()
        self.assertEqual(m.call_count, 0)

    def test_notify_registered_users_game_lifecycle_started(self):
        self.game.start_time = timezone.now() + timedelta(hours=1)
        self.game.end_time = timezone.now() + timedelta(hours=3)
        self.game.save()
        Registration.objects.create(game=self.game, team=self.team)
        old = Game.objects.get(pk=self.game.id)
        new = Game.objects.get(pk=self.game.id)
        new.start_time = timezone.now() - timedelta(minutes=1)
        layer = MagicMock()
        layer.group_send = AsyncMock(return_value=None)
        with patch('games.views.track.get_channel_layer', return_value=layer):
            with patch('games.views.track.notify_user_after_commit') as nu:
                with self.captureOnCommitCallbacks(execute=True):
                    notify_registered_users_game_lifecycle_changed(old, new)
        layer.group_send.assert_called_once()
        args, _kwargs = layer.group_send.call_args
        self.assertEqual(args[0], CHANNEL_GROUPS['game'](self.game.id))
        self.assertEqual(args[1]['type'], 'track.event')
        self.assertEqual(args[1]['event'], 'game.started')
        self.assertEqual(args[1]['payload']['game_id'], self.game.id)
        self.assertIn('seq', args[1])
        nu.assert_called_once()

    def test_notify_registered_users_game_lifecycle_ended(self):
        self.game.start_time = timezone.now() - timedelta(hours=2)
        self.game.end_time = timezone.now() + timedelta(hours=1)
        self.game.save()
        Registration.objects.create(game=self.game, team=self.team)
        old = Game.objects.get(pk=self.game.id)
        new = Game.objects.get(pk=self.game.id)
        new.end_time = timezone.now() - timedelta(minutes=1)
        layer = MagicMock()
        layer.group_send = AsyncMock(return_value=None)
        with patch('games.views.track.get_channel_layer', return_value=layer):
            with patch('games.views.track.notify_user_after_commit') as nu:
                with self.captureOnCommitCallbacks(execute=True):
                    notify_registered_users_game_lifecycle_changed(old, new)
        layer.group_send.assert_called_once()
        args, _kwargs = layer.group_send.call_args
        self.assertEqual(args[1]['event'], 'game.ended')
        nu.assert_called_once()

    def test_notify_registered_users_play_access_changed_unit(self):
        self.game.is_tournament = True
        self.game.is_ready = False
        self.game.is_registrable = True
        self.game.is_playable = True
        self.game.start_time = timezone.now() - timedelta(hours=1)
        self.game.end_time = timezone.now() + timedelta(hours=1)
        self.game.save()
        Registration.objects.create(game=self.game, team=self.team)
        old = Game.objects.get(pk=self.game.id)
        new = Game.objects.get(pk=self.game.id)
        new.is_ready = True
        with patch('games.views.track.notify_user_after_commit') as m:
            notify_registered_users_play_access_changed(old, new)
        self.assertEqual(m.call_count, 1)


@override_settings(
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
)
class TrackWebsocketIntegrationTests(TrackGameFixtureMixin, TestCase):
    """
    Full ASGI stack: session cookie auth, consumer groups, channel layer delivery.

    TrackGame is an AsyncJsonWebsocketConsumer so group_add runs on the same event
    loop as WebsocketCommunicator + InMemoryChannelLayer in tests.
    """

    def _session_headers(self):
        client = Client()
        client.force_login(self.user)
        session_id = client.cookies['sessionid'].value
        return [
            (b'cookie', f'sessionid={session_id}'.encode()),
            (b'host', b'testserver'),
        ]

    def test_websocket_receives_team_group_message(self):
        from interoves_django.asgi import application

        headers = self._session_headers()
        path = f'/games/{self.game.id}/track/'

        async def run():
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _sub = await communicator.connect()
            assert connected
            layer = get_channel_layer()
            self.assertTrue(layer.groups)

            group_name = CHANNEL_GROUPS['game_team'](
                self.game.id,
                self.team.get_name_hash(),
            )
            await layer.group_send(
                group_name,
                {
                    'type': 'task.changed',
                    'task': self.task.id,
                    'by': 'team',
                    'integration': 'team_group',
                },
            )
            msg = await communicator.receive_json_from(timeout=5)
            assert msg['by'] == 'team'
            assert msg['integration'] == 'team_group'
            assert 'seq' in msg

            await communicator.disconnect()

        async_to_sync(run)()

    def test_websocket_receives_game_group_message(self):
        from interoves_django.asgi import application

        headers = self._session_headers()
        path = f'/games/{self.game.id}/track/'

        async def run():
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _ = await communicator.connect()
            assert connected
            layer = get_channel_layer()
            self.assertTrue(layer.groups)

            group_name = CHANNEL_GROUPS['game'](self.game.id)
            await layer.group_send(
                group_name,
                {
                    'type': 'task.changed',
                    'task': self.task.id,
                    'by': 'team',
                    'integration': 'game_group',
                },
            )
            msg = await communicator.receive_json_from(timeout=5)
            assert msg['integration'] == 'game_group'
            assert 'seq' in msg

            await communicator.disconnect()

        async_to_sync(run)()

    def test_websocket_receives_user_group_message(self):
        from interoves_django.asgi import application

        headers = self._session_headers()
        path = f'/games/{self.game.id}/track/'

        async def run():
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _ = await communicator.connect()
            assert connected
            layer = get_channel_layer()
            user_group = CHANNEL_GROUPS['user'](self.user.id)
            self.assertIn(user_group, layer.groups)

            await layer.group_send(
                user_group,
                {
                    'type': 'track.event',
                    'event': 'integration.user_group',
                    'payload': {'ok': True},
                },
            )
            msg = await communicator.receive_json_from(timeout=5)
            self.assertEqual(msg['type'], 'track.event')
            self.assertEqual(msg['event'], 'integration.user_group')
            self.assertTrue(msg['payload']['ok'])
            self.assertIn('seq', msg)

            await communicator.disconnect()

        async_to_sync(run)()

    def test_websocket_receives_build_event_task_change_payload(self):
        """
        Same JSON shape as track_task_change → group_send, delivered on one event loop.

        Note: calling track_task_change() from a sync TestCase schedules on_commit,
        then async_to_sync(group_send) from that thread — InMemoryChannelLayer may not
        deliver to a WebsocketCommunicator on another loop. Production uses Redis or a
        single process; the on_commit path is covered by TrackChannelTests.
        """
        from interoves_django.asgi import application

        headers = self._session_headers()
        path = f'/games/{self.game.id}/track/'

        async def run():
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _ = await communicator.connect()
            assert connected

            def build_event():
                return build_event_task_change(
                    self.task,
                    team=self.team,
                    update_html={'e2e': True},
                )

            event = await database_sync_to_async(build_event)()
            layer = get_channel_layer()
            await layer.group_send(
                CHANNEL_GROUPS['game_team'](self.game.id, self.team.get_name_hash()),
                event,
            )
            msg = await communicator.receive_json_from(timeout=5)
            await communicator.disconnect()
            return msg

        msg = async_to_sync(run)()
        self.assertEqual(msg['type'], 'task.changed')
        self.assertTrue(msg['e2e'])
        self.assertIn('seq', msg)

    def test_user_track_websocket_receives_user_group(self):
        from interoves_django.asgi import application

        headers = self._session_headers()
        path = '/ws/track/'

        async def run():
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _ = await communicator.connect()
            assert connected
            layer = get_channel_layer()
            user_group = CHANNEL_GROUPS['user'](self.user.id)
            await layer.group_send(
                user_group,
                {
                    'type': 'track.event',
                    'event': 'integration.user_hub',
                    'payload': {'x': 1},
                },
            )
            msg = await communicator.receive_json_from(timeout=5)
            self.assertEqual(msg['type'], 'track.event')
            self.assertEqual(msg['event'], 'integration.user_hub')
            self.assertEqual(msg['payload']['x'], 1)
            self.assertIn('seq', msg)
            await communicator.disconnect()

        async_to_sync(run)()
