"""
Tests for WebSocket track groups and deferred channel sends (on_commit).

Integration tests use WebsocketCommunicator + session cookie; TrackGame is async
so InMemoryChannelLayer groups line up with the test event loop.
"""
import asyncio
import time
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
    Attempt,
    CheckerType,
    Game,
    GameTaskGroup,
    Hint,
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
    current_track_seq,
    envelope_track_message,
    game_track_namespace,
    msgpack_safe_keys,
    next_track_seq,
    notify_registered_users_game_lifecycle_changed,
    notify_registered_users_play_access_changed,
    notify_user_after_commit,
    team_track_namespace,
    track_attempt_change,
    track_task_change,
    user_track_namespace,
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
                'is_ready': True,
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


def _msgpack_redis_roundtrip(message):
    """Как channels_redis MsgPackSerializer: packb + unpackb(strict_map_key=True)."""
    import msgpack

    packed = msgpack.packb(message, use_bin_type=True)
    return msgpack.unpackb(packed, raw=False, strict_map_key=True)


class MsgpackSafeTrackPayloadTests(TestCase):
    def test_unsanitized_int_keys_break_redis_msgpack_unpack(self):
        """Регрессия прод-бага: int keys в update_task_html_new → ValueError на receive."""
        import msgpack

        raw = {
            'type': 'task.changed',
            'task': 6137,
            'by': 'team',
            'update_task_html_new': {6137: '<div id="new-task-6137"></div>'},
        }
        packed = msgpack.packb(raw, use_bin_type=True)
        with self.assertRaises(ValueError) as ctx:
            msgpack.unpackb(packed, raw=False, strict_map_key=True)
        self.assertIn('strict_map_key', str(ctx.exception))

    def test_msgpack_safe_keys_stringifies_int_map_keys(self):
        raw = {
            'type': 'task.changed',
            'update_task_html_new': {6137: '<div id="new-task-6137"></div>'},
            'nested': [{1: 'a'}],
        }
        safe = msgpack_safe_keys(raw)
        self.assertEqual(safe['update_task_html_new']['6137'], raw['update_task_html_new'][6137])
        self.assertEqual(safe['nested'][0]['1'], 'a')
        out = _msgpack_redis_roundtrip(safe)
        self.assertEqual(out['update_task_html_new']['6137'], raw['update_task_html_new'][6137])

    def test_envelope_track_message_sanitizes_int_keys(self):
        body = {
            'type': 'task.changed',
            'task': 42,
            'by': 'team',
            'update_task_html_new': {99: 'html'},
        }
        out = envelope_track_message(body, 'any_game_id')
        self.assertIn('99', out['update_task_html_new'])
        self.assertNotIn(99, out['update_task_html_new'])
        self.assertIn('seq', out)
        self.assertEqual(out['seq_namespace'], 'game:any_game_id')
        # Полный путь envelope → Redis msgpack не должен падать.
        again = _msgpack_redis_roundtrip(out)
        self.assertEqual(again['update_task_html_new']['99'], 'html')


@override_settings(DEFER_CHANNEL_BROADCAST=False)
class TrackChannelTests(TrackGameFixtureMixin, TestCase):
    """Синхронная отправка в Channels, чтобы captureOnCommitCallbacks видел group_send сразу."""

    def test_attempt_save_does_not_broadcast_as_admin(self):
        with patch('games.views.track.track_task_change') as track:
            Attempt.manager.create(
                task=self.task,
                game=self.game,
                team=self.team,
                text='wrong',
                status='Wrong',
                points=0,
            )
        track.assert_not_called()

    def test_task_save_notifies_after_updated_row_is_persisted(self):
        observed_text = []

        def observe_saved_task(task):
            observed_text.append(Task.objects.get(pk=task.pk).text)

        self.task.text = 'Corrected task text'
        with patch('games.views.track.track_task_change', side_effect=observe_saved_task):
            self.task.save(update_fields=['text'])

        self.assertEqual(observed_text, ['Corrected task text'])

    def test_hint_save_notifies_after_updated_row_is_persisted(self):
        with patch('games.views.track.track_task_change'):
            hint = Hint.objects.create(task=self.task, number='1', text='Old hint')
        observed_text = []

        def observe_saved_hint(_task):
            observed_text.append(Hint.objects.get(pk=hint.pk).text)

        hint.text = 'Corrected hint text'
        with patch('games.views.track.track_task_change', side_effect=observe_saved_hint):
            hint.save(update_fields=['text'])

        self.assertEqual(observed_text, ['Corrected hint text'])

    def test_channel_group_names(self):
        self.assertEqual(CHANNEL_GROUPS['game']('g1'), 'track.game.g1')
        self.assertEqual(
            CHANNEL_GROUPS['game_team']('g1', 42),
            'track.game.g1.team_id.42',
        )
        self.assertEqual(
            CHANNEL_GROUPS['game_team_legacy']('g1', 'abc'),
            'track.game.g1.team.abc',
        )
        self.assertEqual(CHANNEL_GROUPS['user'](42), 'track.user.42')

    def test_next_track_seq_monotonic(self):
        from django.core.cache import caches

        ns = 'unit_test_seq_namespace'
        caches['track_revisions'].delete(f'track:seq:{ns}')
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
        self.assertEqual(layer.group_send.call_count, 2)
        stable_call, legacy_call = layer.group_send.call_args_list
        args = stable_call.args
        self.assertEqual(args[0], CHANNEL_GROUPS['game_team'](self.game.id, self.team.pk))
        self.assertEqual(legacy_call.args[0], CHANNEL_GROUPS['game_team_legacy'](
            self.game.id, self.team.get_name_hash(),
        ))
        self.assertEqual(args[1], legacy_call.args[1])
        self.assertEqual(args[1]['type'], 'task.changed')
        self.assertEqual(args[1]['stub'], True)
        self.assertIn('seq', args[1])
        self.assertEqual(
            args[1]['seq_namespace'],
            team_track_namespace(self.game.id, self.team.pk),
        )
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
        self.assertEqual(args[1]['seq_namespace'], game_track_namespace(self.game.id))

    @override_settings(
        CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
    )
    def test_track_task_change_sends_personal_html_only_to_user_group(self):
        layer = MagicMock()
        layer.group_send = AsyncMock(return_value=None)
        with patch('games.views.track.get_channel_layer', return_value=layer):
            with self.captureOnCommitCallbacks(execute=True):
                track_task_change(
                    self.task,
                    user=self.user,
                    update_html={'personal': 1},
                )
        layer.group_send.assert_called_once()
        args, _kwargs = layer.group_send.call_args
        self.assertEqual(args[0], CHANNEL_GROUPS['user'](self.user.id))
        self.assertEqual(args[1]['by'], 'personal')
        self.assertEqual(args[1]['personal'], 1)
        self.assertEqual(args[1]['seq_namespace'], user_track_namespace(self.user.id))

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
        self.assertEqual(args[1]['seq_namespace'], 'user:{}'.format(self.user.id))

    def test_track_attempt_change_uses_central_actor_hook(self):
        attempt = Attempt(
            task=self.task,
            game=self.game,
            team=self.team,
            text='pending',
            status='Pending',
        )
        with patch('games.views.track.track_task_change') as notify:
            track_attempt_change(attempt, reason='attempt.reviewed')
        notify.assert_called_once_with(
            self.task,
            team=self.team,
            user=None,
            anon_key=None,
            game=self.game,
            reason='attempt.reviewed',
            current_mode=None,
            update_html=None,
            request=None,
        )

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
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
    TRACK_WS_IDLE_TIMEOUT=0,
)
class TrackWebsocketIntegrationTests(TrackGameFixtureMixin, TestCase):
    """
    Full ASGI stack: session cookie auth, consumer groups, channel layer delivery.

    TrackGame is an AsyncJsonWebsocketConsumer so group_add runs on the same event
    loop as WebsocketCommunicator + InMemoryChannelLayer in tests.
    """

    def _session_headers(self, user=None):
        client = Client()
        client.force_login(user or self.user)
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
                self.team.pk,
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

    def test_two_teammates_receive_same_team_event(self):
        from interoves_django.asgi import application

        teammate = User.objects.create_user('track_ws_teammate', password='pw')
        Profile.objects.create(
            user=teammate,
            first_name='Team',
            last_name='Mate',
            team_on=self.team,
        )
        first_headers = self._session_headers(self.user)
        second_headers = self._session_headers(teammate)
        path = f'/games/{self.game.id}/track/'

        async def run():
            first = WebsocketCommunicator(application, path, headers=first_headers)
            second = WebsocketCommunicator(application, path, headers=second_headers)
            self.assertTrue((await first.connect())[0])
            self.assertTrue((await second.connect())[0])
            await get_channel_layer().group_send(
                CHANNEL_GROUPS['game_team'](self.game.id, self.team.pk),
                {
                    'type': 'task.changed',
                    'task': self.task.id,
                    'by': 'team',
                    'integration': 'two_teammates',
                },
            )
            first_msg = await first.receive_json_from(timeout=5)
            second_msg = await second.receive_json_from(timeout=5)
            self.assertEqual(first_msg['integration'], 'two_teammates')
            self.assertEqual(second_msg['integration'], 'two_teammates')
            await first.disconnect()
            await second.disconnect()

        async_to_sync(run)()

    def test_websocket_receives_legacy_team_group_during_rolling_deploy(self):
        from interoves_django.asgi import application

        headers = self._session_headers()
        path = f'/games/{self.game.id}/track/'

        async def run():
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _ = await communicator.connect()
            assert connected
            layer = get_channel_layer()
            await layer.group_send(
                CHANNEL_GROUPS['game_team_legacy'](
                    self.game.id, self.team.get_name_hash(),
                ),
                {
                    'type': 'task.changed',
                    'task': self.task.id,
                    'by': 'team',
                    'integration': 'legacy_team_group',
                },
            )
            msg = await communicator.receive_json_from(timeout=5)
            self.assertEqual(msg['integration'], 'legacy_team_group')
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
                CHANNEL_GROUPS['game_team'](self.game.id, self.team.pk),
                event,
            )
            msg = await communicator.receive_json_from(timeout=5)
            await communicator.disconnect()
            return msg

        msg = async_to_sync(run)()
        self.assertEqual(msg['type'], 'task.changed')
        self.assertTrue(msg['e2e'])
        self.assertIn('seq', msg)

    def test_websocket_receives_task_html_map_like_production(self):
        """
        Канал /games/<id>/track: payload как после send_attempt (update_task_html_new),
        через envelope (как track_task_change) → group_send → клиент.
        Int keys в map раньше роняли Redis msgpack; envelope обязан их починить.
        """
        from interoves_django.asgi import application

        headers = self._session_headers()
        path = f'/games/{self.game.id}/track/'
        tid = self.task.id
        html = f'<article id="new-task-{tid}">ok</article>'

        async def run():
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _ = await communicator.connect()
            assert connected

            # Как build_event_task_change + update_task_html до фикса ключей (int keys).
            raw_event = {
                'type': 'task.changed',
                'task': tid,
                'by': 'team',
                'update_task_html_new': {tid: html},
            }
            event = envelope_track_message(raw_event, self.game.id)
            # Redis-слой в проде; здесь проверяем и msgpack, и доставку по сокету.
            _msgpack_redis_roundtrip(event)

            layer = get_channel_layer()
            await layer.group_send(
                CHANNEL_GROUPS['game_team'](self.game.id, self.team.pk),
                event,
            )
            msg = await communicator.receive_json_from(timeout=5)
            await communicator.disconnect()
            return msg

        msg = async_to_sync(run)()
        self.assertEqual(msg['type'], 'task.changed')
        self.assertEqual(msg['by'], 'team')
        self.assertEqual(msg['task'], tid)
        self.assertIn('seq', msg)
        self.assertEqual(msg['update_task_html_new'][str(tid)], html)

    def test_websocket_connects_without_team(self):
        """Страница анонса тоже ставит data-track-game-id — сокет не должен падать без команды."""
        from interoves_django.asgi import application

        lone = User.objects.create_user('track_no_team', 'track_no_team@example.com', 'pw')
        Profile.objects.create(user=lone, first_name='N', last_name='T', team_on=None)
        client = Client()
        client.force_login(lone)
        headers = [
            (b'cookie', f'sessionid={client.cookies["sessionid"].value}'.encode()),
            (b'host', b'testserver'),
        ]
        path = f'/games/{self.game.id}/track/'

        async def run():
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _ = await communicator.connect()
            assert connected
            layer = get_channel_layer()
            self.assertIn(CHANNEL_GROUPS['game'](self.game.id), layer.groups)
            self.assertIn(CHANNEL_GROUPS['user'](lone.id), layer.groups)
            await communicator.disconnect()

        async_to_sync(run)()

    def test_websocket_rejects_unknown_or_inaccessible_game(self):
        from interoves_django.asgi import application

        closed_game = Game.objects.create(
            id='closed_track_game',
            name='Closed',
            author='test',
            is_ready=False,
        )
        headers = self._session_headers()

        async def cannot_connect(path):
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _ = await communicator.connect()
            self.assertFalse(connected)

        async_to_sync(cannot_connect)(f'/games/{closed_game.id}/track/')
        async_to_sync(cannot_connect)('/games/missing_track_game/track/')

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

    def test_user_track_ping_pong(self):
        from interoves_django.asgi import application

        headers = self._session_headers()
        path = '/ws/track/'

        async def run():
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _ = await communicator.connect()
            assert connected
            await communicator.send_json_to({'type': 'ping'})
            msg = await communicator.receive_json_from(timeout=5)
            self.assertEqual(msg, {'type': 'pong'})
            await communicator.disconnect()

        async_to_sync(run)()

    def test_game_track_reconnect_detects_missed_team_revision(self):
        from interoves_django.asgi import application

        headers = self._session_headers()
        path = f'/games/{self.game.id}/track/'
        namespace = team_track_namespace(self.game.id, self.team.pk)

        async def baseline():
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _ = await communicator.connect()
            assert connected
            await communicator.send_json_to({'type': 'track.sync', 'seen': {}})
            msg = await communicator.receive_json_from(timeout=5)
            await communicator.disconnect()
            return msg

        first = async_to_sync(baseline)()
        self.assertEqual(first['type'], 'track.synced')
        self.assertEqual(first['versions'][namespace], current_track_seq(namespace))

        next_track_seq(namespace)

        async def reconnect():
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _ = await communicator.connect()
            assert connected
            await communicator.send_json_to({
                'type': 'track.sync',
                'seen': first['versions'],
            })
            msg = await communicator.receive_json_from(timeout=5)
            await communicator.disconnect()
            return msg

        second = async_to_sync(reconnect)()
        self.assertEqual(second['type'], 'track.resync_required')
        self.assertEqual(second['missed'][namespace], current_track_seq(namespace))

    @override_settings(TRACK_WS_GROUP_DISCARD_TIMEOUT=0.05, TRACK_WS_IDLE_TIMEOUT=0)
    def test_disconnect_bounded_when_group_discard_hangs(self):
        """Slow Redis group_discard must not block disconnect beyond TRACK_WS_GROUP_DISCARD_TIMEOUT."""
        from interoves_django.asgi import application

        headers = self._session_headers()
        path = '/ws/track/'

        async def slow_discard(group, channel):
            await asyncio.sleep(30)

        async def run():
            communicator = WebsocketCommunicator(application, path, headers=headers)
            connected, _ = await communicator.connect()
            assert connected
            layer = get_channel_layer()
            with patch.object(layer, 'group_discard', side_effect=slow_discard):
                t0 = time.monotonic()
                await communicator.disconnect()
                elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 2.0)

        async_to_sync(run)()
