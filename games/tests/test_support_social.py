from io import BytesIO

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image
from unittest.mock import patch

from games.models import HTMLPage, Profile, Project
from games.social.models import SocialQueuePost
from games.support.constants import SUPPORT_CONSOLE_GROUP


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
        with patch('games.support.services.social.publish_twitter') as tw:
            with patch('games.support.services.social.publish_telegram') as tg:
                with patch('games.support.services.social.publish_instagram') as ig:
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

    @patch('games.support.services.social.publish_twitter')
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
        pk = post.pk
        response = self.client.post(reverse('support:social_delete', args=[pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertFalse(SocialQueuePost.objects.filter(pk=pk).exists())

    @override_settings(
        TELEGRAM_API_ID=1, TELEGRAM_API_HASH='h', TELEGRAM_USER_SESSION='s',
        TELEGRAM_CHANNEL_CHAT_ID='@chan',
    )
    @patch('games.support.services.social.fetch_scheduled_message_sync')
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
    @patch('games.support.services.social.fetch_scheduled_message_sync')
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
