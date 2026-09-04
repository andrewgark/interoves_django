from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialLogin
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from games.account_merge import (
    PENDING_ACCOUNT_MERGE_SESSION_KEY,
    social_provider_label,
)
from games.models import HTMLPage, Profile, Project
from games.signals import _avatar_url_from_extra
from games.users.allauth import SocialAccountAdapter


def _ensure_login_modal_deps():
    Project.objects.get_or_create(pk='main', defaults={})
    Project.objects.get_or_create(pk='glowbyte', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    site, _ = Site.objects.get_or_create(
        id=1, defaults={'domain': 'testserver', 'name': 'test'},
    )
    for provider, name in (('google', 'Google'), ('vk', 'VK'), ('yandex', 'Yandex')):
        app, _ = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
        )
        app.sites.add(site)


class YandexAuthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_login_modal_deps()

    def test_yandex_login_url_is_registered(self):
        self.assertEqual(reverse('yandex_login'), '/accounts/yandex/login/')
        self.assertEqual(
            reverse('yandex_callback'),
            '/accounts/yandex/login/callback/',
        )

    def test_provider_label_is_russian(self):
        self.assertEqual(social_provider_label('yandex'), 'Яндекс')
        self.assertEqual(social_provider_label('google'), 'Google')

    def test_login_modal_includes_yandex(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Войти через Яндекс')
        self.assertContains(response, reverse('yandex_login'))
        self.assertContains(response, 'Войти через Google')
        self.assertContains(response, 'Войти через VK')
        self.assertContains(response, 'Войти через Telegram')

    def test_glowbyte_profile_offers_yandex_connect(self):
        user = User.objects.create_user('glowbyte-profile', password='secret')
        Profile.objects.create(user=user, first_name='Gb', last_name='User')
        SocialAccount.objects.create(
            user=user,
            provider='google',
            uid='google-glowbyte-profile',
            extra_data={'email': 'anna@glowbyteconsulting.com'},
        )
        client = Client()
        client.force_login(user)
        response = client.get(
            reverse('project_profile', kwargs={'project_id': 'glowbyte'}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '@glowbyte.ru')
        self.assertContains(response, 'Подключить')
        self.assertContains(response, reverse('yandex_login'))
        self.assertNotContains(response, 'new-account-icon--telegram')
        self.assertNotContains(response, 'new-account-icon--vk')
        response = self.client.get(
            reverse('project_hub', kwargs={'project_id': 'glowbyte'}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Войти через Google')
        self.assertContains(response, 'Войти через Яндекс')
        self.assertContains(response, '@glowbyteconsulting.com')
        self.assertContains(response, '@glowbyte.ru')
        self.assertNotContains(response, 'Войти через VK')
        self.assertNotContains(response, 'Войти через Telegram')

    def _glowbyte_login(self, provider, email, extra_data=None):
        sociallogin = SocialLogin(
            user=User(username='glowbyte-{}'.format(provider), email=email),
            account=SocialAccount(
                provider=provider,
                uid='{}-glowbyte'.format(provider),
                extra_data=extra_data or {},
            ),
        )
        sociallogin.state = {'process': 'login', 'next': '/glowbyte/games/'}
        return sociallogin

    def test_adapter_allows_yandex_glowbyte_ru_email(self):
        sociallogin = self._glowbyte_login(
            'yandex',
            'anna@glowbyte.ru',
            extra_data={'default_email': 'anna@glowbyte.ru'},
        )
        SocialAccountAdapter().pre_social_login(RequestFactory().get('/'), sociallogin)

    def test_adapter_allows_yandex_glowbyte_ru_from_extra_data(self):
        sociallogin = SocialLogin(
            user=User(username='glowbyte-yandex-extra', email=''),
            account=SocialAccount(
                provider='yandex',
                uid='yandex-glowbyte-extra',
                extra_data={'default_email': 'anna@glowbyte.ru'},
            ),
        )
        sociallogin.state = {'process': 'login', 'next': '/glowbyte/'}
        SocialAccountAdapter().pre_social_login(RequestFactory().get('/'), sociallogin)

    def test_adapter_rejects_yandex_without_glowbyte_ru_email(self):
        sociallogin = self._glowbyte_login(
            'yandex',
            'ivan@yandex.ru',
            extra_data={'default_email': 'ivan@yandex.ru'},
        )
        with self.assertRaises(ImmediateHttpResponse) as caught:
            SocialAccountAdapter().pre_social_login(RequestFactory().get('/'), sociallogin)
        self.assertEqual(
            caught.exception.response.url,
            '/glowbyte/?auth=email_not_allowed',
        )

    def test_adapter_rejects_google_without_consulting_email(self):
        sociallogin = self._glowbyte_login('google', 'anna@glowbyte.ru')
        with self.assertRaises(ImmediateHttpResponse) as caught:
            SocialAccountAdapter().pre_social_login(RequestFactory().get('/'), sociallogin)
        self.assertEqual(
            caught.exception.response.url,
            '/glowbyte/?auth=email_not_allowed',
        )

    def test_adapter_allows_google_consulting_email(self):
        sociallogin = self._glowbyte_login('google', 'anna@glowbyteconsulting.com')
        SocialAccountAdapter().pre_social_login(RequestFactory().get('/'), sociallogin)

    def test_adapter_still_rejects_vk_on_glowbyte(self):
        sociallogin = self._glowbyte_login('vk', 'anna@glowbyte.ru')
        with self.assertRaises(ImmediateHttpResponse) as caught:
            SocialAccountAdapter().pre_social_login(RequestFactory().get('/'), sociallogin)
        self.assertEqual(
            caught.exception.response.url,
            '/glowbyte/?auth=vk_not_allowed',
        )

    def test_signal_fills_yandex_email_and_avatar(self):
        user = User.objects.create_user('yandex-user', password='secret')
        SocialAccount.objects.create(
            user=user,
            provider='yandex',
            uid='12345',
            extra_data={
                'first_name': 'Иван',
                'last_name': 'Иванов',
                'default_email': 'ivan@yandex.ru',
                'default_avatar_id': 'abc123',
                'is_avatar_empty': False,
            },
        )
        profile = Profile.objects.get(user=user)
        self.assertEqual(profile.first_name, 'Иван')
        self.assertEqual(profile.last_name, 'Иванов')
        self.assertEqual(profile.email, 'ivan@yandex.ru')
        self.assertEqual(
            profile.avatar_url,
            'https://avatars.yandex.net/get-yapic/abc123/islands-200',
        )

    def test_signal_skips_empty_yandex_avatar(self):
        self.assertEqual(
            _avatar_url_from_extra({
                'default_avatar_id': '0/0-0',
                'is_avatar_empty': True,
            }),
            '',
        )

    def test_profile_shows_yandex_connect_and_default_email(self):
        user = User.objects.create_user(
            'yandex-profile', 'yandex-profile@example.com', 'secret',
        )
        Profile.objects.create(user=user, first_name='Ya', last_name='User')
        SocialAccount.objects.create(
            user=user,
            provider='google',
            uid='google-yandex-profile',
            extra_data={'email': 'yandex-profile@example.com'},
        )
        SocialAccount.objects.create(
            user=user,
            provider='yandex',
            uid='yandex-profile-uid',
            extra_data={
                'default_email': 'ivan@yandex.ru',
                'display_name': 'Ivan',
                'login': 'ivan',
            },
        )
        client = Client()
        client.force_login(user)
        response = client.get(reverse('ui_profile'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Яндекс')
        self.assertContains(response, 'ivan@yandex.ru')
        self.assertContains(response, 'data-provider-label="Яндекс"')

    def test_merge_confirm_uses_yandex_label(self):
        target = User.objects.create_user('yandex-target', password='secret')
        source = User.objects.create_user('yandex-source', password='secret')
        Profile.objects.create(user=target, first_name='Target', last_name='Ya')
        Profile.objects.create(user=source, first_name='Source', last_name='Ya')
        SocialAccount.objects.create(
            user=target, provider='google', uid='google-yandex-target',
        )
        SocialAccount.objects.create(
            user=source, provider='yandex', uid='yandex-source',
        )
        client = Client()
        client.force_login(target)
        session = client.session
        session[PENDING_ACCOUNT_MERGE_SESSION_KEY] = {
            'target_user_id': target.pk,
            'source_user_id': source.pk,
            'provider': 'yandex',
            'provider_uid': 'yandex-source',
            'created_at': __import__('time').time(),
            'nonce': 'yandex-nonce',
            'next': '/profile/',
        }
        session.save()

        response = client.get(reverse('ui_account_merge_confirm'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Яндекс')

    def test_disconnect_uses_yandex_label(self):
        user = User.objects.create_user('yandex-disconnect', password='secret')
        Profile.objects.create(user=user, first_name='D', last_name='Y')
        SocialAccount.objects.create(
            user=user, provider='google', uid='google-yandex-disconnect',
        )
        yandex = SocialAccount.objects.create(
            user=user, provider='yandex', uid='yandex-disconnect',
        )
        client = Client()
        client.force_login(user)
        response = client.post(
            reverse('ui_social_account_disconnect'),
            {'account_id': yandex.pk, 'next': '/profile/'},
            follow=True,
        )
        self.assertContains(response, 'Яндекс отключён')
        self.assertFalse(SocialAccount.objects.filter(pk=yandex.pk).exists())

    def test_authentication_error_copy_is_provider_agnostic(self):
        response = self.client.get(reverse('socialaccount_login_error'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Не удалось подтвердить вход')
        self.assertNotContains(response, 'Google или VK')
