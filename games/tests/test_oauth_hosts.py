"""OAuth on interoves.com and ru.interoves.com.

Both hostnames serve one app and one database, but are separate origins with
separate host-only sessions. Each OAuth flow must use the redirect_uri of the
host it started on (registered with the provider), keep its state in that
host's session, and end on that host.
"""
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.models import Site
from django.template import Context, Template
from django.test import Client, RequestFactory, TestCase, override_settings

from games.identity.allauth import SocialAccountAdapter
from games.tests.test_yandex_auth import _ensure_login_modal_deps

HOSTS = ('interoves.com', 'ru.interoves.com')

# Callback paths as routed by allauth (``<provider>_callback``) and by
# games.identity.telegram_oidc.
CALLBACK_PATHS = {
    'google': '/accounts/google/login/callback/',
    'yandex': '/accounts/yandex/login/callback/',
    'vk': '/accounts/vk/login/callback/',
    'telegram': '/accounts/telegram/callback/',
}

PROD_OAUTH = {
    'ACCOUNT_DEFAULT_HTTP_PROTOCOL': 'https',
    'OAUTH_CALLBACK_HOSTS': HOSTS,
}


def _ensure_apps():
    _ensure_login_modal_deps()
    app, _ = SocialApp.objects.get_or_create(
        provider='telegram',
        defaults={'name': 'Telegram', 'client_id': 'test', 'secret': 'test'},
    )
    app.sites.add(Site.objects.get(id=settings.SITE_ID))


def _query(url):
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def _fake_yandex_session(captured):
    token = MagicMock(status_code=200, headers={'content-type': 'application/json'})
    token.json.return_value = {'access_token': 'yandex-token', 'token_type': 'bearer'}
    profile = MagicMock()
    profile.json.return_value = {
        'id': '424242', 'login': 'oauth-host', 'default_email': 'oauth-host@yandex.ru',
        'first_name': 'Ой', 'last_name': 'Авторизация',
    }
    session = MagicMock()

    def request(method, url, **kwargs):
        captured.append(kwargs.get('data') or kwargs.get('params') or {})
        return token

    session.request.side_effect = request
    session.get.return_value = profile
    return session


@override_settings(**PROD_OAUTH)
class OAuthLoginStartTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_apps()

    def test_redirect_uri_matches_the_starting_host_for_every_provider(self):
        for host in HOSTS:
            for provider, callback in CALLBACK_PATHS.items():
                with self.subTest(host=host, provider=provider):
                    client = Client(HTTP_HOST=host)
                    response = client.get(f'/accounts/{provider}/login/', {'next': '/salad/'})
                    self.assertEqual(response.status_code, 302)
                    params = _query(response['Location'])
                    self.assertEqual(params['redirect_uri'], f'https://{host}{callback}')
                    self.assertTrue(params.get('state'))
                    states = client.session['socialaccount_states']
                    self.assertEqual(states[params['state']][0]['next'], '/salad/')

    def test_telegram_uses_pkce(self):
        client = Client(HTTP_HOST='ru.interoves.com')
        params = _query(client.get('/accounts/telegram/login/')['Location'])
        self.assertEqual(params['code_challenge_method'], 'S256')
        self.assertTrue(params['code_challenge'])
        state = client.session['socialaccount_states'][params['state']][0]
        self.assertTrue(state['pkce_code_verifier'])

    def test_next_on_another_host_is_dropped(self):
        for host, foreign in (('ru.interoves.com', 'https://interoves.com/salad/'),
                              ('interoves.com', 'https://ru.interoves.com/salad/'),
                              ('ru.interoves.com', '/\\evil.example/')):
            with self.subTest(host=host, foreign=foreign):
                client = Client(HTTP_HOST=host)
                params = _query(client.get('/accounts/yandex/login/', {'next': foreign})['Location'])
                state = client.session['socialaccount_states'][params['state']][0]
                self.assertNotIn('next', state)

    def test_unregistered_host_moves_the_start_to_the_main_site(self):
        host = 'interoves-env.eu-central-1.elasticbeanstalk.com'
        response = Client(HTTP_HOST=host).get('/accounts/google/login/', {'next': '/salad/'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            settings.SITE_BASE_URL + '/accounts/google/login/?next=%2Fsalad%2F',
        )

    def test_unregistered_host_refuses_callbacks(self):
        client = Client(HTTP_HOST='127.0.0.1')
        for callback in CALLBACK_PATHS.values():
            with self.subTest(callback=callback):
                self.assertEqual(client.get(callback, {'code': 'c', 'state': 's'}).status_code, 400)

    @override_settings(OAUTH_CALLBACK_HOSTS=())
    def test_development_keeps_any_allowed_host(self):
        response = Client(HTTP_HOST='localhost').get('/accounts/yandex/login/')
        self.assertEqual(
            _query(response['Location'])['redirect_uri'],
            'https://localhost/accounts/yandex/login/callback/',
        )


@override_settings(**PROD_OAUTH)
class OAuthCallbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_apps()

    def _start(self, host, next_url='/salad/'):
        client = Client(HTTP_HOST=host)
        params = _query(client.get('/accounts/yandex/login/', {'next': next_url})['Location'])
        return client, params['state']

    def _callback(self, client, state, captured):
        with patch.object(
            SocialAccountAdapter,
            'get_requests_session',
            return_value=_fake_yandex_session(captured),
        ):
            return client.get(CALLBACK_PATHS['yandex'], {'code': 'auth-code', 'state': state})

    def test_code_exchange_uses_the_same_redirect_uri_and_ends_on_the_host(self):
        for host in HOSTS:
            with self.subTest(host=host):
                client, state = self._start(host)
                captured = []
                response = self._callback(client, state, captured)
                self.assertEqual(response.status_code, 302)
                # Relative: the browser stays on the host it started on.
                self.assertEqual(response['Location'], '/salad/')
                self.assertEqual(
                    captured[0]['redirect_uri'],
                    f'https://{host}/accounts/yandex/login/callback/',
                )
                self.assertEqual(captured[0]['code'], 'auth-code')
                home = client.get('/')
                self.assertContains(home, 'data-authenticated="1"')
                session_cookie = client.cookies[settings.SESSION_COOKIE_NAME]
                self.assertEqual(session_cookie['domain'], '')
                self.assertEqual(session_cookie['samesite'], 'Lax')
                client.logout()

    def test_state_from_another_hosts_session_is_rejected(self):
        _main, state = self._start('interoves.com')
        ru = Client(HTTP_HOST='ru.interoves.com')
        captured = []
        response = self._callback(ru, state, captured)
        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(captured, [], 'no code exchange without a matching state')
        self.assertContains(ru.get('/'), 'data-authenticated="0"')

    def test_state_is_single_use(self):
        client, state = self._start('ru.interoves.com')
        self._callback(client, state, [])
        client.logout()
        captured = []
        response = self._callback(client, state, captured)
        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(captured, [])


class LoginNextTests(TestCase):
    def _auth_next(self, path):
        request = RequestFactory().get(path)
        return Template('{% load filters %}{% auth_next %}').render(Context({'request': request}))

    def test_auth_next_keeps_local_paths_only(self):
        self.assertEqual(self._auth_next('/x/?next=/salad/%3Fa%3D1'), '/salad/?a=1')
        for bad in ('//evil.example/', '/\\evil.example/', 'https://interoves.com/'):
            with self.subTest(bad=bad):
                # Falls back to the current page instead of the foreign target.
                self.assertTrue(self._auth_next('/x/?next=' + bad).startswith('/x/?next='))

    def test_accounts_login_encodes_next(self):
        response = Client().get('/accounts/login/', {'next': '/salad/?a=1&login=0'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(_query(response['Location']), {'login': '1', 'next': '/salad/?a=1&login=0'})
