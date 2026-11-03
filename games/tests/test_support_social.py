from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from games.console.http.constants import SUPPORT_CONSOLE_GROUP
from games.models import HTMLPage, Profile, Project
from games.ops.social.models import SocialQueuePost


def _png_upload(name='paste.png', size=(32, 40)):
    buf = BytesIO()
    Image.new('RGB', size, (10, 20, 30)).save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT='/tmp/interoves_test_media_social')
class SupportSocialQueueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        cls.staff = User.objects.create_user('social_staff', 's@example.com', 'secret')
        Profile.objects.create(user=cls.staff, first_name='S', last_name='T')
        group, _ = Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)
        group.user_set.add(cls.staff)

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='social_staff', password='secret'))

    def test_dashboard_renders(self):
        response = self.client.get(reverse('support:social'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Посты')
        self.assertContains(response, 'Ctrl+V')

    def test_create_with_image_upload(self):
        response = self.client.post(
            reverse('support:social_create'),
            {
                'caption': 'тест из буфера',
                'image': _png_upload(),
                'mode': 'draft',
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['post']['caption'], 'тест из буфера')
        self.assertTrue(data['post']['image_url'])
        post = SocialQueuePost.objects.get(pk=data['post']['id'])
        self.assertEqual(post.source, SocialQueuePost.SOURCE_MANUAL)
        self.assertTrue(post.image)
        self.assertEqual(post.telegram_status, SocialQueuePost.STATUS_PENDING)
        self.assertEqual(post.twitter_status, SocialQueuePost.STATUS_PENDING)
        self.assertEqual(post.instagram_status, SocialQueuePost.STATUS_PENDING)

    def test_create_draft_does_not_publish(self):
        with patch('games.console.feed.social.publish_twitter') as tw:
            with patch('games.console.feed.social.publish_telegram') as tg:
                with patch('games.console.feed.social.publish_instagram') as ig:
                    response = self.client.post(
                        reverse('support:social_create'),
                        {
                            'caption': 'draft only',
                            'image': _png_upload(),
                            'mode': 'draft',
                            'networks': ['telegram', 'twitter', 'instagram'],
                        },
                    )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        tw.assert_not_called()
        tg.assert_not_called()
        ig.assert_not_called()

    def test_create_internal_queues_selected(self):
        response = self.client.post(
            reverse('support:social_create'),
            {
                'caption': 'queued',
                'image': _png_upload(),
                'mode': 'internal',
                'schedule_at': '2026-07-08T16:30:00',
                'networks': ['twitter', 'instagram'],
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        post = SocialQueuePost.objects.get(pk=data['post']['id'])
        self.assertEqual(post.twitter_status, SocialQueuePost.STATUS_QUEUED)
        self.assertEqual(post.instagram_status, SocialQueuePost.STATUS_QUEUED)
        self.assertEqual(post.telegram_status, SocialQueuePost.STATUS_PENDING)

    def test_create_internal_queues_threads(self):
        response = self.client.post(
            reverse('support:social_create'),
            {
                'caption': 'threads',
                'image': _png_upload(),
                'mode': 'internal',
                'schedule_at': '2026-07-08T16:30:00',
                'networks': ['threads'],
            },
        )
        self.assertEqual(response.status_code, 200)
        post = SocialQueuePost.objects.get(pk=response.json()['post']['id'])
        self.assertEqual(post.threads_status, SocialQueuePost.STATUS_QUEUED)
        self.assertTrue(post.threads_queued_for)

    @override_settings(THREADS_ACCESS_TOKEN='token', SITE_BASE_URL='https://example.com')
    @patch('games.ops.social.publish.publish_threads_image_url', return_value='thread-123')
    def test_publish_threads(self, publish_mock):
        from games.ops.social.publish import publish_threads

        post = SocialQueuePost.objects.create(
            caption='hello', source=SocialQueuePost.SOURCE_MANUAL,
        )
        post.image.save('a.png', _png_upload(), save=True)
        publish_threads(post)
        post.refresh_from_db()
        self.assertEqual(post.threads_status, SocialQueuePost.STATUS_SENT)
        self.assertEqual(post.threads_external_id, 'thread-123')
        publish_mock.assert_called_once()

    @patch('games.console.feed.social.publish_twitter')
    def test_publish_endpoint_calls_network(self, tw_mock):
        post = SocialQueuePost.objects.create(caption='x', source=SocialQueuePost.SOURCE_MANUAL)
        post.image.save('a.png', _png_upload(), save=True)

        def _side_effect(p, force=False):
            p.twitter_status = SocialQueuePost.STATUS_SENT
            p.twitter_external_id = '99'
            p.save(update_fields=['twitter_status', 'twitter_external_id'])
            return p

        tw_mock.side_effect = _side_effect
        response = self.client.post(
            reverse('support:social_publish', args=[post.pk]),
            data='{"network":"twitter","force":false,"action":"publish"}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['post']['twitter']['status'], 'sent')
        tw_mock.assert_called_once()

    def test_delete_post(self):
        post = SocialQueuePost.objects.create(caption='bye', source=SocialQueuePost.SOURCE_MANUAL)
        post.image.save('a.png', _png_upload(), save=True)
        post.social_image.save('a-social.png', _png_upload('a-social.png'), save=True)
        pk = post.pk
        response = self.client.post(reverse('support:social_delete', args=[pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertFalse(SocialQueuePost.objects.filter(pk=pk).exists())

    @override_settings(
        TELEGRAM_API_ID=1, TELEGRAM_API_HASH='h', TELEGRAM_USER_SESSION='s',
        TELEGRAM_CHANNEL_CHAT_ID='@chan',
    )
    @patch('games.console.feed.social.fetch_scheduled_message_sync')
    def test_sync_telegram_pulls_caption(self, fetch_mock):
        post = SocialQueuePost.objects.create(
            caption='старый текст',
            source=SocialQueuePost.SOURCE_MANUAL,
            telegram_status=SocialQueuePost.STATUS_SCHEDULED,
            telegram_external_id='4242',
        )
        fetch_mock.return_value = {
            'message_id': 4242,
            'caption': 'новый текст из телеги',
            'caption_plain': 'новый текст из телеги',
            'date': None,
        }
        response = self.client.post(
            reverse('support:social_sync_telegram', args=[post.pk])
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['post']['caption'], 'новый текст из телеги')
        fetch_mock.assert_called_once()
        self.assertEqual(fetch_mock.call_args.kwargs['message_id'], 4242)
        post.refresh_from_db()
        self.assertEqual(post.caption, 'новый текст из телеги')

    def test_sync_telegram_requires_external_id(self):
        post = SocialQueuePost.objects.create(
            caption='no tg', source=SocialQueuePost.SOURCE_MANUAL,
        )
        response = self.client.post(
            reverse('support:social_sync_telegram', args=[post.pk])
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])

    @override_settings(
        TELEGRAM_API_ID=1, TELEGRAM_API_HASH='h', TELEGRAM_USER_SESSION='s',
        TELEGRAM_CHANNEL_CHAT_ID='@chan',
    )
    @patch('games.console.feed.social.fetch_scheduled_message_sync')
    def test_sync_telegram_not_found_returns_error(self, fetch_mock):
        post = SocialQueuePost.objects.create(
            caption='gone',
            source=SocialQueuePost.SOURCE_MANUAL,
            telegram_status=SocialQueuePost.STATUS_SCHEDULED,
            telegram_external_id='7',
        )
        fetch_mock.return_value = None
        response = self.client.post(
            reverse('support:social_sync_telegram', args=[post.pk])
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])

    def test_list_orders_planned_first_then_created(self):
        from datetime import datetime
        from datetime import timezone as dt_timezone

        from games.console.feed.social import list_posts

        def _aware(y, mo, d, h=0):
            return datetime(y, mo, d, h, tzinfo=dt_timezone.utc)

        _draft_old = SocialQueuePost.objects.create(caption='draft old')
        _draft_new = SocialQueuePost.objects.create(caption='draft new')
        _planned_soon = SocialQueuePost.objects.create(
            caption='planned soon',
            twitter_status=SocialQueuePost.STATUS_QUEUED,
            twitter_queued_for=_aware(2026, 8, 1, 10),
        )
        _planned_later = SocialQueuePost.objects.create(
            caption='planned later',
            telegram_status=SocialQueuePost.STATUS_SCHEDULED,
            telegram_scheduled_for=_aware(2026, 9, 1, 10),
        )

        captions = [p['caption'] for p in list_posts()]
        # planned group first, posting time descending (later on top)
        self.assertLess(
            captions.index('planned later'), captions.index('planned soon')
        )
        # planned group entirely above the draft group
        self.assertLess(
            captions.index('planned soon'), captions.index('draft new')
        )
        self.assertLess(
            captions.index('planned soon'), captions.index('draft old')
        )
        # drafts by created_at descending (newest first)
        self.assertLess(
            captions.index('draft new'), captions.index('draft old')
        )

    def test_queue_endpoint_sets_internal_schedule(self):
        post = SocialQueuePost.objects.create(caption='q', source=SocialQueuePost.SOURCE_MANUAL)
        post.image.save('a.png', _png_upload(), save=True)
        response = self.client.post(
            reverse('support:social_publish', args=[post.pk]),
            data=(
                '{"network":"twitter","action":"queue",'
                '"schedule_at":"2026-07-08T16:30:00"}'
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['post']['twitter']['status'], 'queued')
        self.assertTrue(data['post']['twitter']['queued_for'])
        post.refresh_from_db()
        self.assertEqual(post.twitter_status, SocialQueuePost.STATUS_QUEUED)
        self.assertIsNotNone(post.twitter_queued_for)


@override_settings(MEDIA_ROOT='/tmp/interoves_test_media_social')
class SupportSocialRegenerateImageTests(TestCase):
    """The web process only enqueues; Chromium runs on the integrations worker."""

    @classmethod
    def setUpTestData(cls):
        from games.models import Task, TaskGroup

        cls.staff = User.objects.create_user('regen_staff', 'r@example.com', 'secret')
        Profile.objects.create(user=cls.staff, first_name='R', last_name='S')
        group, _ = Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)
        group.user_set.add(cls.staff)
        task_group = TaskGroup.objects.create(label='regen-ladder')
        cls.task = Task.objects.create(task_group=task_group, number='1', task_type='raddle')

    def setUp(self):
        from django.core.cache import caches

        caches['support_jobs'].clear()
        self.client = Client()
        self.assertTrue(self.client.login(username='regen_staff', password='secret'))
        self.post = SocialQueuePost.objects.create(
            caption='лесенка', source=SocialQueuePost.SOURCE_LADDER, task=self.task, ladder_number=7,
        )

    def _regenerate(self, post_id=None):
        return self.client.post(reverse('support:social_regenerate_image', args=[post_id or self.post.pk]))

    def _status(self, job_id):
        return self.client.get(
            reverse('support:social_regenerate_image_status', args=[self.post.pk]), {'job': job_id},
        )

    def test_button_enqueues_without_rendering_in_web(self):
        with patch('games.ops.telegram.bot.alerts.enqueue_integrations_message', return_value=True) as enqueue, \
                patch('games.console.feed.social.regenerate_post_image') as render:
            response = self._regenerate()
        self.assertEqual(response.status_code, 200)
        job = response.json()['job']
        self.assertEqual(job['status'], 'queued')
        render.assert_not_called()
        kwargs = enqueue.call_args.kwargs
        self.assertEqual(kwargs['message_type'], 'social.regenerate_image')
        self.assertEqual(kwargs['payload'], {'post_id': self.post.pk, 'job_id': job['job_id']})
        self.assertEqual(self._status(job['job_id']).json()['job']['status'], 'queued')

    def test_second_click_while_pending_reuses_the_job(self):
        with patch('games.ops.telegram.bot.alerts.enqueue_integrations_message', return_value=True) as enqueue:
            first = self._regenerate().json()['job']
            second = self._regenerate().json()['job']
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual(enqueue.call_count, 1)

    def test_enqueue_failure_is_reported_and_not_left_pending(self):
        with patch('games.ops.telegram.bot.alerts.enqueue_integrations_message', return_value=False):
            response = self._regenerate()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])
        from games.console.feed.social import regenerate_image_state

        self.assertIsNone(regenerate_image_state(self.post.pk))

    def test_post_without_task_is_rejected_before_enqueue(self):
        manual = SocialQueuePost.objects.create(caption='x', source=SocialQueuePost.SOURCE_MANUAL)
        with patch('games.ops.telegram.bot.alerts.enqueue_integrations_message') as enqueue:
            response = self._regenerate(manual.pk)
        self.assertEqual(response.status_code, 400)
        enqueue.assert_not_called()

    def test_worker_job_renders_and_status_returns_post(self):
        from games.console.feed.social import run_regenerate_image_job

        with patch('games.ops.telegram.bot.alerts.enqueue_integrations_message', return_value=True):
            job = self._regenerate().json()['job']
        with patch('games.console.feed.social.regenerate_post_image') as render:
            result = run_regenerate_image_job(payload={'post_id': self.post.pk, 'job_id': job['job_id']})
        self.assertEqual(result['status'], 'done')
        self.assertEqual(render.call_args.args[0].pk, self.post.pk)
        data = self._status(job['job_id']).json()
        self.assertEqual(data['job']['status'], 'done')
        self.assertEqual(data['post']['id'], self.post.pk)

    def test_worker_job_failure_is_recorded_for_the_console(self):
        from games.console.feed.social import SocialSupportError, run_regenerate_image_job

        with patch('games.ops.telegram.bot.alerts.enqueue_integrations_message', return_value=True):
            job = self._regenerate().json()['job']
        with patch(
            'games.console.feed.social.regenerate_post_image',
            side_effect=SocialSupportError('Ошибка Playwright-рендера: boom'),
        ):
            result = run_regenerate_image_job(payload={'post_id': self.post.pk, 'job_id': job['job_id']})
        self.assertEqual(result['status'], 'failed')
        state = self._status(job['job_id']).json()['job']
        self.assertEqual(state['status'], 'failed')
        self.assertIn('boom', state['error'])

    def test_superseded_job_does_not_render(self):
        from games.console.feed.social import run_regenerate_image_job

        with patch('games.ops.telegram.bot.alerts.enqueue_integrations_message', return_value=True):
            self._regenerate()
        with patch('games.console.feed.social.regenerate_post_image') as render:
            result = run_regenerate_image_job(payload={'post_id': self.post.pk, 'job_id': 'old'})
        self.assertEqual(result['status'], 'superseded')
        render.assert_not_called()

    def _render_with_telegram(self, *, replaced):
        from games.console.feed.social import regenerate_post_image

        png = b'\x89PNG fake'
        with patch('games.ops.telegram.images.ladder.render_ladder_teaser_png', return_value=png), \
                patch('games.console.feed.social.telegram_user_configured', return_value=True), \
                patch('games.console.feed.social.telegram_channel_configured', return_value=True), \
                patch('games.console.feed.social.channel_chat_id', return_value='@chan'), \
                patch('games.console.feed.social.edit_scheduled_photo_sync', return_value=replaced) as edit:
            outcome = regenerate_post_image(self.post)
        return outcome, edit

    def test_scheduled_telegram_photo_is_replaced_even_if_twitter_already_sent(self):
        self.post.telegram_status = SocialQueuePost.STATUS_SCHEDULED
        self.post.telegram_external_id = '555'
        self.post.twitter_status = SocialQueuePost.STATUS_SENT
        self.post.save()
        outcome, edit = self._render_with_telegram(replaced=True)
        self.assertEqual(outcome, 'replaced')
        self.assertEqual(edit.call_args.kwargs['message_id'], 555)
        self.post.refresh_from_db()
        self.assertTrue(self.post.image.name)

    def test_already_published_telegram_post_still_saves_image_and_warns(self):
        from games.console.feed.social import run_regenerate_image_job

        self.post.telegram_status = SocialQueuePost.STATUS_SCHEDULED
        self.post.telegram_external_id = '555'
        self.post.save()
        outcome, _edit = self._render_with_telegram(replaced=False)
        self.assertEqual(outcome, 'already_published')
        self.post.refresh_from_db()
        self.assertTrue(self.post.image.name)

        with patch('games.ops.telegram.bot.alerts.enqueue_integrations_message', return_value=True):
            job = self._regenerate().json()['job']
        with patch('games.console.feed.social.regenerate_post_image', return_value='already_published'):
            run_regenerate_image_job(payload={'post_id': self.post.pk, 'job_id': job['job_id']})
        state = self._status(job['job_id']).json()['job']
        self.assertEqual(state['telegram'], 'already_published')
        self.assertIn('уже вышел', state['note'])

    def test_unscheduled_post_does_not_touch_telegram(self):
        outcome, edit = self._render_with_telegram(replaced=True)
        self.assertEqual(outcome, 'not_scheduled')
        edit.assert_not_called()

    def test_unknown_job_status_is_404(self):
        self.assertEqual(self._status('nope').status_code, 404)
