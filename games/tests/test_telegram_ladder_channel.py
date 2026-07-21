import json
import os
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone

from games.ladder_daily import LADDER_PUBLISH_START_TAG
from games.models import Game, GameTaskGroup, Project, Task, TaskGroup
from games.telegram.ladder_channel import (
    build_caption,
    process_ladder_channel_tick,
    publish_at_for_date,
    resolve_today_ladder,
    schedule_ladder_channel_post,
)
from games.telegram.models import TelegramLadderChannelPost
from games.tests.test_raddle import PARIS_LADDER


@override_settings(
    TELEGRAM_BOT_TOKEN='test-token',
    TELEGRAM_ADMIN_CHAT_ID='12345',
    TELEGRAM_CHANNEL_CHAT_ID='@interoves',
    TELEGRAM_API_ID=12345,
    TELEGRAM_API_HASH='hash',
    TELEGRAM_USER_SESSION='session-string',
    TELEGRAM_LADDER_SCREENSHOT=False,
    SITE_BASE_URL='https://interoves.com',
)
class LadderChannelScheduleTests(TestCase):
    def setUp(self):
        Project.objects.get_or_create(id='sections')
        self.game, _ = Game.objects.update_or_create(
            id='ladder',
            defaults={
                'name': 'Лесенка',
                'author': 'a',
                'project_id': 'sections',
                'tags': {LADDER_PUBLISH_START_TAG: '2026-07-08T00:00:00+03:00'},
            },
        )
        self.tg = TaskGroup.objects.create(label='ladder-channel-1')
        self.task = Task.objects.create(
            task_group=self.tg,
            number='1',
            task_type='raddle',
            checker_data=json.dumps(PARIS_LADDER, ensure_ascii=False),
            tags={'author': 'Test Author'},
        )
        GameTaskGroup.objects.filter(game=self.game).delete()
        GameTaskGroup.objects.create(
            game=self.game, task_group=self.tg, number='1', name='№1',
        )
        self.now = datetime(2026, 7, 8, 0, 15, tzinfo=ZoneInfo('Europe/Moscow'))

    def test_resolve_today_ladder(self):
        ladder = resolve_today_ladder(self.now)
        self.assertIsNotNone(ladder)
        self.assertEqual(ladder.number, 1)
        self.assertEqual(ladder.task.pk, self.task.pk)
        self.assertIn('/games/ladder/1/', ladder.play_url)

    def test_publish_at(self):
        at = publish_at_for_date(self.now.date())
        self.assertEqual(at.hour, 16)
        self.assertEqual(at.minute, 30)
        self.assertEqual(str(at.tzinfo), 'Europe/Moscow')

    def test_caption_and_image(self):
        ladder = resolve_today_ladder(self.now)
        caption = build_caption(ladder)
        self.assertIn('Лесенка №1', caption)
        self.assertIn('ПАРИЖ', caption)
        self.assertIn('ДАКАР', caption)
        from games.telegram.ladder_image import render_ladder_teaser_png_pillow
        png = render_ladder_teaser_png_pillow(self.task, ladder_number=1)
        self.assertTrue(png.startswith(b'\x89PNG'))

    @patch('games.telegram.ladder_channel.send_photo')
    @patch('games.telegram.ladder_channel.schedule_channel_photo_sync')
    def test_schedule_uses_mtproto_schedule_date(self, mtproto_mock, admin_photo_mock):
        mtproto_mock.return_value = {'message_id': 42, 'scheduled': True}
        admin_photo_mock.return_value = {'message_id': 1}

        post = schedule_ladder_channel_post(now=self.now, force=True, notify_admin=True)
        self.assertIsNotNone(post)
        self.assertEqual(post.status, TelegramLadderChannelPost.STATUS_SCHEDULED)
        self.assertEqual(post.telegram_message_id, 42)
        self.assertEqual(post.scheduled_for.hour, 16)
        self.assertEqual(post.scheduled_for.minute, 30)

        kwargs = mtproto_mock.call_args.kwargs
        self.assertEqual(kwargs['chat'], '@interoves')
        self.assertEqual(kwargs['schedule_at'], post.scheduled_for)
        self.assertTrue(kwargs['photo_bytes'].startswith(b'\x89PNG'))
        admin_photo_mock.assert_called_once()

        # Idempotent
        mtproto_mock.reset_mock()
        again = schedule_ladder_channel_post(now=self.now, force=False)
        self.assertEqual(again.pk, post.pk)
        mtproto_mock.assert_not_called()

    @patch('games.telegram.ladder_channel.send_photo')
    @patch('games.telegram.ladder_channel.schedule_channel_photo_sync')
    def test_tick_only_in_0015_window(self, mtproto_mock, _admin_photo_mock):
        mtproto_mock.return_value = {'message_id': 7, 'scheduled': True}
        outside = datetime(2026, 7, 8, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        stats = process_ladder_channel_tick(now=outside)
        self.assertEqual(stats['scheduled'], 0)
        mtproto_mock.assert_not_called()

        stats = process_ladder_channel_tick(now=self.now)
        self.assertEqual(stats['scheduled'], 1)
        mtproto_mock.assert_called_once()

    @patch('games.telegram.ladder_channel.send_photo')
    def test_preview_ladder_to_admin(self, send_photo_mock):
        from games.telegram.ladder_channel import preview_ladder_to_admin

        send_photo_mock.return_value = {'message_id': 99}
        ok, message = preview_ladder_to_admin(now=self.now)
        self.assertTrue(ok)
        self.assertIn('№1', message)
        kwargs = send_photo_mock.call_args.kwargs
        self.assertEqual(send_photo_mock.call_args.args[0], '12345')
        self.assertTrue(send_photo_mock.call_args.args[1].startswith(b'\x89PNG'))
        self.assertIn('Лесенка №1', kwargs['caption'])

    @patch('games.telegram.ladder_channel.schedule_channel_photo_sync')
    def test_schedule_refuses_after_1630(self, mtproto_mock):
        late = datetime(2026, 7, 8, 19, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        post = schedule_ladder_channel_post(now=late, force=True, notify_admin=False)
        self.assertIsNotNone(post)
        self.assertEqual(post.status, TelegramLadderChannelPost.STATUS_FAILED)
        self.assertIn('refusing to post immediately', post.error)
        mtproto_mock.assert_not_called()


class EnsurePlaywrightBrowsersPathTests(TestCase):
    def test_sets_eb_webapp_cache_when_env_missing(self):
        from games.telegram import ladder_image as li

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('PLAYWRIGHT_BROWSERS_PATH', None)
            with patch.object(li.os.path, 'isdir', return_value=True):
                li._ensure_playwright_browsers_path()
            self.assertEqual(
                os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),
                '/home/webapp/.cache/ms-playwright',
            )

    def test_does_not_override_existing_env(self):
        from games.telegram import ladder_image as li

        with patch.dict(os.environ, {'PLAYWRIGHT_BROWSERS_PATH': '/custom/browsers'}):
            with patch.object(li.os.path, 'isdir', return_value=True):
                li._ensure_playwright_browsers_path()
            self.assertEqual(os.environ.get('PLAYWRIGHT_BROWSERS_PATH'), '/custom/browsers')
