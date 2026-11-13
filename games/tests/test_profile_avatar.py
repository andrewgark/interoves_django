import io
import shutil
import tempfile
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount, SocialApp
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from games.community import avatars
from games.community.visibility import is_trusted_avatar_url, public_avatar_url
from games.models import Profile, Project

VK_PHOTO = 'https://sun6-21.userapi.com/big.jpg'


def _png(size=(600, 300), mode='RGBA'):
    out = io.BytesIO()
    Image.new(mode, size, (200, 30, 30, 255) if mode == 'RGBA' else (200, 30, 30)).save(out, format='PNG')
    return out.getvalue()


class AvatarTestBase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls._media, TELEGRAM_BOT_TOKEN='')
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        Project.objects.get_or_create(pk='main', defaults={})
        site = Site.objects.get_current()
        for provider in ('google', 'vk', 'yandex'):
            app, _ = SocialApp.objects.get_or_create(
                provider=provider, defaults={'name': provider, 'client_id': 'x', 'secret': 'x'},
            )
            app.sites.add(site)
        self.user = User.objects.create_user('av_owner', password='secret')
        self.profile = Profile.objects.create(
            user=self.user, first_name='Анна', last_name='Петрова',
            avatar_url='https://lh3.googleusercontent.com/a=s96-c',
        )
        SocialAccount.objects.create(user=self.user, provider='vk', uid='1', extra_data={
            'photo_medium': 'https://sun6-21.userapi.com/medium.jpg', 'photo_big': VK_PHOTO,
        })
        SocialAccount.objects.create(user=self.user, provider='google', uid='2', extra_data={
            'picture': 'https://lh3.googleusercontent.com/a=s96-c',
        })
        self.client = Client()
        self.client.login(username='av_owner', password='secret')

    def post(self, **data):
        return self.client.post(reverse('ui_profile_avatar'), data)


class ProviderSourceTests(AvatarTestBase):
    def test_sources_follow_connected_accounts_vk_first(self):
        sources = avatars.provider_sources(self.user, self.profile)
        self.assertEqual([s.key for s in sources], ['vk', 'google'])
        self.assertEqual(sources[0].url, VK_PHOTO)
        # Google's 96px thumbnail is asked at avatar size.
        self.assertEqual(sources[1].url, 'https://lh3.googleusercontent.com/a=s400-c')

    @override_settings(TELEGRAM_BOT_TOKEN='token')
    def test_bot_linked_telegram_is_offered_without_url(self):
        Profile.objects.filter(pk=self.profile.pk).update(telegram_user_id=777)
        self.profile.refresh_from_db()
        telegram = avatars.provider_sources(self.user, self.profile)[1]
        self.assertEqual((telegram.key, telegram.url), ('telegram', ''))

    def test_oidc_telegram_photo_is_used_directly(self):
        SocialAccount.objects.create(user=self.user, provider='telegram', uid='s', extra_data={
            'picture': 'https://t.me/i/userpic/320/x.jpg',
        })
        response = self.post(action='source:telegram')
        self.assertEqual(response.status_code, 302)
        self.assertIn('edit=1', response['Location'])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.avatar_url, 'https://t.me/i/userpic/320/x.jpg')

    def test_pick_vk(self):
        self.post(action='source:vk')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.avatar_url, VK_PHOTO)

    def test_unconnected_source_is_refused(self):
        self.post(action='source:yandex')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.avatar_url, 'https://lh3.googleusercontent.com/a=s96-c')

    @override_settings(TELEGRAM_BOT_TOKEN='token')
    def test_bot_telegram_photo_is_downloaded_and_stored(self):
        Profile.objects.filter(pk=self.profile.pk).update(telegram_user_id=777)
        with patch.object(avatars, 'fetch_telegram_photo', return_value=_png()) as fetch:
            self.post(action='source:telegram')
        fetch.assert_called_once_with(777)
        self.profile.refresh_from_db()
        self.assertTrue(avatars.is_uploaded_avatar_url(self.profile.avatar_url), self.profile.avatar_url)

    @override_settings(TELEGRAM_BOT_TOKEN='token')
    def test_bot_telegram_without_photo_keeps_current(self):
        Profile.objects.filter(pk=self.profile.pk).update(telegram_user_id=777)
        with patch.object(avatars, 'fetch_telegram_photo', return_value=None):
            response = self.client.post(reverse('ui_profile_avatar'), {'action': 'source:telegram'}, follow=True)
        self.assertContains(response, 'Telegram не отдал фото')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.avatar_url, 'https://lh3.googleusercontent.com/a=s96-c')


class UploadTests(AvatarTestBase):
    def upload(self, data, name='me.png'):
        return self.client.post(reverse('ui_profile_avatar'), {
            'action': 'upload', 'photo': SimpleUploadedFile(name, data, content_type='image/png'),
        }, follow=True)

    def test_upload_is_squared_reencoded_and_public(self):
        self.upload(_png())
        self.profile.refresh_from_db()
        url = self.profile.avatar_url
        name = avatars.uploaded_name(url)
        self.assertTrue(name.startswith('avatars/') and name.endswith('.jpg'), url)
        with default_storage.open(name) as stored, Image.open(stored) as image:
            self.assertEqual((image.format, image.size), ('JPEG', (avatars.AVATAR_SIDE, avatars.AVATAR_SIDE)))
        self.assertTrue(is_trusted_avatar_url(url))
        other = User.objects.create_user('av_other')
        self.assertEqual(public_avatar_url(self.profile, other), url)

    def test_replacing_an_upload_deletes_the_old_file(self):
        self.upload(_png())
        self.profile.refresh_from_db()
        first = avatars.uploaded_name(self.profile.avatar_url)
        self.post(action='source:vk')
        self.assertFalse(default_storage.exists(first))

    def test_not_an_image_is_rejected(self):
        response = self.upload(b'<svg onload=alert(1)></svg>', name='x.svg')
        self.assertContains(response, 'Не получилось прочитать картинку')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.avatar_url, 'https://lh3.googleusercontent.com/a=s96-c')

    def test_remove(self):
        self.post(action='remove')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.avatar_url, '')

    def test_uploaded_url_lookalikes_are_not_trusted(self):
        prefix = avatars._uploaded_prefix()
        for url in (
            prefix + '../secret.jpg', prefix + 'x/../../a.jpg', prefix + 'abc.jpg',
            '/media/other/' + 'a' * 32 + '.jpg', '//evil.example' + prefix + 'a' * 32 + '.jpg',
        ):
            self.assertFalse(is_trusted_avatar_url(url), url)
        self.assertTrue(is_trusted_avatar_url(prefix + 'a' * 32 + '.jpg'))


class VisibilityToggleTests(AvatarTestBase):
    def test_hide_and_show(self):
        self.client.post(reverse('ui_profile_avatar_visibility'), {'hidden': '1'})
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.avatar_hidden)
        self.client.post(reverse('ui_profile_avatar_visibility'), {'hidden': '0'})
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.avatar_hidden)

    def test_profile_page_offers_sources_and_no_url_field(self):
        body = self.client.get(reverse('new_profile') + '?edit=1').content.decode()
        self.assertIn('Из VK', body)
        self.assertIn('Из Google', body)
        self.assertIn('Загрузить', body)
        self.assertIn('Скрыть от других', body)
        self.assertNotIn('Ссылка на фото', body)
        self.assertNotIn('name="avatar_url"', body)
        # The signup-time 96px Google link still counts as "from Google".
        self.assertIn('new-profile-avatar__source--google is-current', body)


class RefreshOnLoginTests(AvatarTestBase):
    def login_refresh(self, provider, **extra):
        from allauth.socialaccount.models import SocialLogin
        from allauth.socialaccount.signals import social_account_updated

        account = SocialAccount.objects.get(user=self.user, provider=provider)
        account.extra_data = extra
        account.save()
        social_account_updated.send(sender=SocialLogin, request=None, sociallogin=SocialLogin(account=account))
        self.profile.refresh_from_db()

    def test_photo_from_this_provider_follows_the_new_link(self):
        self.login_refresh('google', picture='https://lh3.googleusercontent.com/new=s96-c')
        self.assertEqual(self.profile.avatar_url, 'https://lh3.googleusercontent.com/new=s400-c')

    def test_other_provider_photo_is_kept(self):
        self.login_refresh('vk', photo_big='https://sun6-22.userapi.com/new.jpg')
        self.assertEqual(self.profile.avatar_url, 'https://lh3.googleusercontent.com/a=s96-c')

    def test_upload_and_removed_photo_are_kept(self):
        for kept in ('/media/avatars/' + 'a' * 32 + '.jpg', ''):
            Profile.objects.filter(pk=self.profile.pk).update(avatar_url=kept)
            self.login_refresh('google', picture='https://lh3.googleusercontent.com/new=s96-c')
            self.assertEqual(self.profile.avatar_url, kept)
