import json
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from PIL import Image

from games.models import CheckerType, Game, GameTaskGroup, Project, Task, TaskGroup
from games.social.models import SocialQueuePost
from games.telegram.word_salad_channel import (
    build_caption,
    process_salad_channel_tick,
    publish_at_for_date,
    resolve_today_salad,
    schedule_salad_channel_post,
)
from games.word_salad import WORD_SALAD_GAME_ID
from games.word_salad_daily import WORD_SALAD_PUBLISH_START_TAG

MOSCOW = ZoneInfo('Europe/Moscow')

SALAD_DATA = {
    'grid': [
        'В', 'А', 'Р', 'А',
        'О', 'К', 'Т', 'М',
        'Т', 'С', 'Р', 'А',
        'П', 'О', 'М', 'С',
    ],
    'words': ['КОСТРОМА', 'САМАРА'],
}


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
class SaladChannelScheduleTests(TestCase):
    def setUp(self):
        Project.objects.get_or_create(id='sections')
        CheckerType.objects.get_or_create(pk='word_salad')
        self.game, _ = Game.objects.update_or_create(
            id=WORD_SALAD_GAME_ID,
            defaults={
                'name': 'Салат',
                'author': 'a',
                'project_id': 'sections',
                'tags': {WORD_SALAD_PUBLISH_START_TAG: '2026-08-23T00:00:00+03:00'},
            },
        )
        self.tg = TaskGroup.objects.create(label='salad-channel-1')
        self.task = Task.objects.create(
            task_group=self.tg,
            number='1',
            task_type='word_salad',
            checker=CheckerType.objects.get(pk='word_salad'),
            checker_data=json.dumps(SALAD_DATA, ensure_ascii=False),
            text='Города России',
        )
        GameTaskGroup.objects.filter(game=self.game).delete()
        GameTaskGroup.objects.create(
            game=self.game, task_group=self.tg, number='1', name='Салат #1',
        )
        self.now = datetime(2026, 8, 23, 0, 15, tzinfo=MOSCOW)

    def test_resolve_today_salad(self):
        salad = resolve_today_salad(self.now)
        self.assertIsNotNone(salad)
        self.assertEqual(salad.number, 1)
        self.assertEqual(salad.task.pk, self.task.pk)
        self.assertIn('/salad/1/', salad.play_url)

    def test_publish_at_is_two_hours_before_ladder(self):
        at = publish_at_for_date(self.now.date())
        self.assertEqual(at.hour, 14)
        self.assertEqual(at.minute, 30)
        self.assertEqual(str(at.tzinfo), 'Europe/Moscow')

    def test_caption_and_image(self):
        salad = resolve_today_salad(self.now)
        caption = build_caption(salad)
        self.assertIn('Салат №1', caption)
        self.assertIn('Города России', caption)
        self.assertIn('/salad/1/', caption)
        from games.telegram.word_salad_image import render_word_salad_teaser_png_pillow
        png = render_word_salad_teaser_png_pillow(self.task, salad_number=1)
        self.assertTrue(png.startswith(b'\x89PNG'))

    def test_screenshot_url_is_public_salad_last(self):
        from games.telegram.word_salad_image import word_salad_last_screenshot_url
        self.assertEqual(
            word_salad_last_screenshot_url(),
            'https://interoves.com/salad/last/',
        )

    def test_screenshot_css_hides_salad_chrome(self):
        from games.telegram.ladder_image import _SCREENSHOT_HIDE_CSS
        self.assertIn('.new-word-salad__selection', _SCREENSHOT_HIDE_CSS)
        self.assertIn('.new-taskcard__bug-btn', _SCREENSHOT_HIDE_CSS)

    @override_settings(TELEGRAM_LADDER_SCREENSHOT=True)
    @patch('games.telegram.word_salad_image.screenshot_word_salad_last_png')
    @patch('games.telegram.word_salad_image.render_word_salad_teaser_png_pillow')
    def test_render_uses_live_screenshot_not_pillow(self, pillow_mock, shot_mock):
        from games.telegram.word_salad_image import render_word_salad_teaser_png
        shot_mock.return_value = _tiny_png_bytes(180, 220)
        png = render_word_salad_teaser_png(self.task, salad_number=1)
        self.assertEqual(png, shot_mock.return_value)
        shot_mock.assert_called_once()
        pillow_mock.assert_not_called()

    def test_same_date_as_ladder_is_allowed(self):
        SocialQueuePost.objects.create(
            source=SocialQueuePost.SOURCE_LADDER,
            ladder_date=self.now.date(),
            ladder_number=1,
        )
        salad_post = SocialQueuePost.objects.create(
            source=SocialQueuePost.SOURCE_WORD_SALAD,
            ladder_date=self.now.date(),
            ladder_number=1,
        )
        self.assertEqual(salad_post.ladder_date, self.now.date())

    @patch('games.telegram.word_salad_channel.send_photo')
    @patch('games.social.publish.schedule_channel_photo_sync')
    @patch('games.telegram.word_salad_channel.render_word_salad_teaser_png')
    def test_schedule_uses_mtproto_schedule_date(self, render_mock, mtproto_mock, admin_photo_mock):
        render_mock.return_value = _tiny_png_bytes(120, 160)
        mtproto_mock.return_value = {'message_id': 42, 'scheduled': True}
        admin_photo_mock.return_value = {'message_id': 1}

        post = schedule_salad_channel_post(now=self.now, force=True, notify_admin=True)
        self.assertIsNotNone(post)
        self.assertEqual(post.telegram_status, SocialQueuePost.STATUS_SCHEDULED)
        self.assertEqual(post.telegram_external_id, '42')
        scheduled_msk = post.telegram_scheduled_for.astimezone(MOSCOW)
        self.assertEqual(scheduled_msk.hour, 14)
        self.assertEqual(scheduled_msk.minute, 30)
        self.assertEqual(post.source, SocialQueuePost.SOURCE_WORD_SALAD)
        self.assertEqual(post.twitter_status, SocialQueuePost.STATUS_QUEUED)
        self.assertEqual(post.instagram_status, SocialQueuePost.STATUS_QUEUED)
        tw_msk = post.twitter_queued_for.astimezone(MOSCOW)
        self.assertEqual((tw_msk.hour, tw_msk.minute), (14, 30))
        ig_msk = post.instagram_queued_for.astimezone(MOSCOW)
        self.assertEqual((ig_msk.hour, ig_msk.minute), (14, 30))

        kwargs = mtproto_mock.call_args.kwargs
        self.assertEqual(kwargs['chat'], '@interoves')
        self.assertEqual(
            kwargs['schedule_at'].astimezone(MOSCOW),
            scheduled_msk,
        )
        self.assertTrue(kwargs['photo_bytes'].startswith(b'\x89PNG'))
        admin_photo_mock.assert_called_once()

        mtproto_mock.reset_mock()
        again = schedule_salad_channel_post(now=self.now, force=False)
        self.assertEqual(again.pk, post.pk)
        mtproto_mock.assert_not_called()

    @patch('games.telegram.word_salad_channel.send_photo')
    @patch('games.social.publish.schedule_channel_photo_sync')
    @patch('games.telegram.word_salad_channel.render_word_salad_teaser_png')
    def test_tick_only_in_0015_window(self, render_mock, mtproto_mock, _admin_photo_mock):
        render_mock.return_value = _tiny_png_bytes(120, 160)
        mtproto_mock.return_value = {'message_id': 7, 'scheduled': True}
        outside = datetime(2026, 8, 23, 12, 0, tzinfo=MOSCOW)
        stats = process_salad_channel_tick(now=outside)
        self.assertEqual(stats['scheduled'], 0)
        mtproto_mock.assert_not_called()

        stats = process_salad_channel_tick(now=self.now)
        self.assertEqual(stats['scheduled'], 1)
        mtproto_mock.assert_called_once()

    @patch('games.social.publish.schedule_channel_photo_sync')
    @patch('games.telegram.word_salad_channel.render_word_salad_teaser_png')
    def test_schedule_refuses_after_1430(self, render_mock, mtproto_mock):
        render_mock.return_value = _tiny_png_bytes(120, 160)
        late = datetime(2026, 8, 23, 15, 0, tzinfo=MOSCOW)
        post = schedule_salad_channel_post(now=late, force=True, notify_admin=False)
        self.assertIsNotNone(post)
        self.assertEqual(post.telegram_status, SocialQueuePost.STATUS_FAILED)
        self.assertIn('refusing to post immediately', post.telegram_error)
        mtproto_mock.assert_not_called()

    @patch('games.telegram.word_salad_channel.render_word_salad_teaser_png', side_effect=RuntimeError('shot failed'))
    def test_schedule_marks_failed_when_real_screenshot_unavailable(self, _render_mock):
        post = schedule_salad_channel_post(now=self.now, force=True, notify_admin=False)
        self.assertIsNotNone(post)
        self.assertEqual(post.telegram_status, SocialQueuePost.STATUS_FAILED)
        self.assertEqual(post.telegram_error, 'render failed')

    @patch('games.social.publish.post_tweet_with_image')
    @patch('games.social.publish.twitter_configured', return_value=True)
    @patch('games.social.publish.publish_image_url')
    @patch('games.social.publish.publish_configured', return_value=True)
    @patch('games.telegram.word_salad_channel.send_photo')
    @patch('games.social.publish.schedule_channel_photo_sync')
    @patch('games.telegram.word_salad_channel.render_word_salad_teaser_png')
    def test_queue_then_tick_publishes_x_ig(
        self,
        render_mock,
        mtproto_mock,
        _admin_photo_mock,
        _ig_cfg,
        publish_mock,
        _tw_cfg,
        tweet_mock,
    ):
        from games.social.publish import process_social_queue_tick

        render_mock.return_value = _tiny_png_bytes(120, 160)
        mtproto_mock.return_value = {'message_id': 42, 'scheduled': True}
        tweet_mock.return_value = {'data': {'id': '999888777'}}
        publish_mock.return_value = '17999000111'

        post = schedule_salad_channel_post(now=self.now, force=True, notify_admin=False)
        self.assertEqual(post.twitter_status, SocialQueuePost.STATUS_QUEUED)
        self.assertEqual(post.instagram_status, SocialQueuePost.STATUS_QUEUED)
        tweet_mock.assert_not_called()
        publish_mock.assert_not_called()

        noon = datetime(2026, 8, 23, 12, 0, tzinfo=MOSCOW)
        process_social_queue_tick(now=noon)
        post.refresh_from_db()
        self.assertEqual(post.twitter_status, SocialQueuePost.STATUS_QUEUED)
        tweet_mock.assert_not_called()

        at_1430 = datetime(2026, 8, 23, 14, 30, tzinfo=MOSCOW)
        process_social_queue_tick(now=at_1430)
        post.refresh_from_db()
        self.assertEqual(post.twitter_status, SocialQueuePost.STATUS_SENT)
        self.assertEqual(post.twitter_external_id, '999888777')
        self.assertEqual(post.instagram_status, SocialQueuePost.STATUS_SENT)
        self.assertEqual(post.instagram_external_id, '17999000111')
        tweet_mock.assert_called_once()
        publish_mock.assert_called_once()
