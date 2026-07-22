import json
import os
from datetime import datetime
from io import BytesIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from PIL import Image

from games.ladder_daily import LADDER_PUBLISH_START_TAG
from games.models import Game, GameTaskGroup, Project, Task, TaskGroup
from games.social.models import SocialQueuePost
from games.telegram.ladder_channel import (
    build_caption,
    process_ladder_channel_tick,
    publish_at_for_date,
    resolve_today_ladder,
    schedule_ladder_channel_post,
)
from games.tests.test_raddle import PARIS_LADDER


def _tiny_png_bytes(w=40, h=50, color=(20, 40, 60)) -> bytes:
    im = Image.new('RGB', (w, h), color)
    buf = BytesIO()
    im.save(buf, format='PNG')
    return buf.getvalue()


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
    @patch('games.social.publish.schedule_channel_photo_sync')
    def test_schedule_uses_mtproto_schedule_date(self, mtproto_mock, admin_photo_mock):
        mtproto_mock.return_value = {'message_id': 42, 'scheduled': True}
        admin_photo_mock.return_value = {'message_id': 1}

        post = schedule_ladder_channel_post(now=self.now, force=True, notify_admin=True)
        self.assertIsNotNone(post)
        self.assertEqual(post.telegram_status, SocialQueuePost.STATUS_SCHEDULED)
        self.assertEqual(post.telegram_external_id, '42')
        scheduled_msk = post.telegram_scheduled_for.astimezone(ZoneInfo('Europe/Moscow'))
        self.assertEqual(scheduled_msk.hour, 16)
        self.assertEqual(scheduled_msk.minute, 30)
        self.assertEqual(post.source, SocialQueuePost.SOURCE_LADDER)
        self.assertEqual(post.twitter_status, SocialQueuePost.STATUS_QUEUED)
        self.assertEqual(post.instagram_status, SocialQueuePost.STATUS_QUEUED)
        tw_msk = post.twitter_queued_for.astimezone(ZoneInfo('Europe/Moscow'))
        self.assertEqual((tw_msk.hour, tw_msk.minute), (16, 30))
        ig_msk = post.instagram_queued_for.astimezone(ZoneInfo('Europe/Moscow'))
        self.assertEqual((ig_msk.hour, ig_msk.minute), (16, 30))

        kwargs = mtproto_mock.call_args.kwargs
        self.assertEqual(kwargs['chat'], '@interoves')
        self.assertEqual(
            kwargs['schedule_at'].astimezone(ZoneInfo('Europe/Moscow')),
            scheduled_msk,
        )
        self.assertTrue(kwargs['photo_bytes'].startswith(b'\x89PNG'))
        admin_photo_mock.assert_called_once()

        mtproto_mock.reset_mock()
        again = schedule_ladder_channel_post(now=self.now, force=False)
        self.assertEqual(again.pk, post.pk)
        mtproto_mock.assert_not_called()
        self.assertEqual(again.twitter_status, SocialQueuePost.STATUS_QUEUED)
        self.assertEqual(again.instagram_status, SocialQueuePost.STATUS_QUEUED)

    @patch('games.telegram.ladder_channel.send_photo')
    @patch('games.social.publish.schedule_channel_photo_sync')
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

    @patch('games.social.publish.schedule_channel_photo_sync')
    def test_schedule_refuses_after_1630(self, mtproto_mock):
        late = datetime(2026, 7, 8, 19, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        post = schedule_ladder_channel_post(now=late, force=True, notify_admin=False)
        self.assertIsNotNone(post)
        self.assertEqual(post.telegram_status, SocialQueuePost.STATUS_FAILED)
        self.assertIn('refusing to post immediately', post.telegram_error)
        mtproto_mock.assert_not_called()

    @patch('games.social.publish.post_tweet_with_image')
    @patch('games.social.publish.twitter_configured', return_value=True)
    @patch('games.social.publish.publish_image_url')
    @patch('games.social.publish.publish_configured', return_value=True)
    @patch('games.telegram.ladder_channel.send_photo')
    @patch('games.social.publish.schedule_channel_photo_sync')
    def test_queue_then_tick_publishes_x_ig(
        self,
        mtproto_mock,
        _admin_photo_mock,
        _ig_cfg,
        publish_mock,
        _tw_cfg,
        tweet_mock,
    ):
        from games.social.publish import process_social_queue_tick

        mtproto_mock.return_value = {'message_id': 42, 'scheduled': True}
        tweet_mock.return_value = {'data': {'id': '999888777'}}
        publish_mock.return_value = '17999000111'

        post = schedule_ladder_channel_post(now=self.now, force=True, notify_admin=False)
        self.assertEqual(post.twitter_status, SocialQueuePost.STATUS_QUEUED)
        self.assertEqual(post.instagram_status, SocialQueuePost.STATUS_QUEUED)
        tweet_mock.assert_not_called()
        publish_mock.assert_not_called()

        # Before 16:30 — tick does nothing for these
        noon = datetime(2026, 7, 8, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        process_social_queue_tick(now=noon)
        post.refresh_from_db()
        self.assertEqual(post.twitter_status, SocialQueuePost.STATUS_QUEUED)
        tweet_mock.assert_not_called()

        at_1630 = datetime(2026, 7, 8, 16, 30, tzinfo=ZoneInfo('Europe/Moscow'))
        process_social_queue_tick(now=at_1630)
        post.refresh_from_db()
        self.assertEqual(post.twitter_status, SocialQueuePost.STATUS_SENT)
        self.assertEqual(post.twitter_external_id, '999888777')
        self.assertEqual(post.instagram_status, SocialQueuePost.STATUS_SENT)
        self.assertEqual(post.instagram_external_id, '17999000111')
        tweet_mock.assert_called_once()
        publish_mock.assert_called_once()
        self.assertIn('/social/queue/{}/instagram.jpg'.format(post.pk), publish_mock.call_args.args[0])


class InstagramJpegRatioTests(TestCase):
    def test_keeps_portrait_45_without_padding(self):
        from games.instagram.api import to_instagram_jpeg

        # 1080x1350 = 0.8 = 4:5
        png = _tiny_png_bytes(108, 135)
        jpeg = to_instagram_jpeg(png)
        im = Image.open(BytesIO(jpeg))
        self.assertEqual(im.size, (108, 135))
        self.assertEqual(im.format, 'JPEG')

    def test_pads_when_too_tall(self):
        from games.instagram.api import to_instagram_jpeg

        # 100x200 = 0.5 < 0.8 → pad to square
        png = _tiny_png_bytes(100, 200)
        jpeg = to_instagram_jpeg(png)
        im = Image.open(BytesIO(jpeg))
        self.assertEqual(im.size, (200, 200))


@override_settings(
    TELEGRAM_BOT_TOKEN='test-token',
    TELEGRAM_ADMIN_CHAT_ID='12345',
    TELEGRAM_CHANNEL_CHAT_ID='@interoves',
    TELEGRAM_API_ID=12345,
    TELEGRAM_API_HASH='hash',
    TELEGRAM_USER_SESSION='session-string',
    SITE_BASE_URL='https://interoves.com',
)
class SocialPublishRetryTests(TestCase):
    def setUp(self):
        self.post = SocialQueuePost.objects.create(
            source=SocialQueuePost.SOURCE_MANUAL,
            caption='hello <b>world</b>',
        )
        self.post.image.save('t.png', ContentFile(_tiny_png_bytes()), save=True)

    @patch('games.social.publish.post_tweet_with_image')
    @patch('games.social.publish.twitter_configured', return_value=True)
    def test_twitter_idempotent_without_force(self, _cfg, tweet_mock):
        from games.social.publish import publish_twitter

        tweet_mock.return_value = {'data': {'id': '111'}}
        publish_twitter(self.post, force=False)
        self.post.refresh_from_db()
        self.assertEqual(self.post.twitter_external_id, '111')
        tweet_mock.reset_mock()
        publish_twitter(self.post, force=False)
        tweet_mock.assert_not_called()
        tweet_mock.return_value = {'data': {'id': '222'}}
        publish_twitter(self.post, force=True)
        tweet_mock.assert_called_once()
        self.post.refresh_from_db()
        self.assertEqual(self.post.twitter_external_id, '222')


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
