"""interoves.com and ru.interoves.com serve the same application.

ru.interoves.com reaches the origin through a Russian reverse proxy. Browsers
there cannot rely on interoves.com (Cloudflare) or third-party CDNs, so every
asset, API call and socket of a rendered page must stay on the page's host.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from games.models import (
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    Task,
    TaskGroup,
    Team,
)
from games.tests.test_yandex_auth import _ensure_login_modal_deps

MAIN_HOST = 'interoves.com'
RU_HOST = 'ru.interoves.com'
HOSTS = (MAIN_HOST, RU_HOST)

# Functional third-party integrations that are not interface dependencies.
# They are loaded on demand by the pages that need them and stay external.
EXTERNAL_INTEGRATION_HOSTS = {
    'mc.yandex.ru',  # Yandex Metrika (analytics)
    'yookassa.ru',  # YooKassa checkout widget (payments)
}

# Mirrors the prod STATIC_URL shape (settings.py, USE_S3 + IS_PROD).
PROD_LIKE_STATIC_URL = '/static/releases/test-release/'

CSS_URL_RE = re.compile(r'url\(\s*([\'"]?)([^\'")]+)\1\s*\)')
CSS_IMPORT_RE = re.compile(r'@import\s+(?:url\()?\s*[\'"]?([^\'")\s;]+)')


class _AssetCollector(HTMLParser):
    """Collect URLs the browser fetches while rendering a page."""

    def __init__(self):
        super().__init__()
        self.assets = []
        self.stylesheets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'img', 'source', 'iframe') and attrs.get('src'):
            self.assets.append((tag, attrs['src']))
        if tag == 'link' and attrs.get('href'):
            self.assets.append((tag, attrs['href']))
            if 'stylesheet' in (attrs.get('rel') or '').split():
                self.stylesheets.append(attrs['href'])


def _collect(html):
    parser = _AssetCollector()
    parser.feed(html)
    return parser


def _local_static_path(url):
    """Map a rendered static URL back to its source file."""
    path = urlsplit(url).path
    if not path.startswith(settings.STATIC_URL):
        return None
    return finders.find(path[len(settings.STATIC_URL):])


class HostSettingsTests(SimpleTestCase):
    def test_both_hosts_are_allowed(self):
        for host in HOSTS:
            self.assertIn(host, settings.ALLOWED_HOSTS)

    def test_both_https_origins_are_trusted_for_csrf(self):
        self.assertIn('https://interoves.com', settings.CSRF_TRUSTED_ORIGINS)
        self.assertIn('https://ru.interoves.com', settings.CSRF_TRUSTED_ORIGINS)

    def test_cookies_stay_host_only(self):
        # Each origin keeps its own session; a parent-domain cookie would
        # leak interoves.com sessions to every subdomain.
        self.assertIsNone(settings.SESSION_COOKIE_DOMAIN)
        self.assertIsNone(settings.CSRF_COOKIE_DOMAIN)

    def test_untrusted_forwarding_headers_are_ignored(self):
        self.assertFalse(settings.USE_X_FORWARDED_HOST)
        self.assertIsNone(settings.SECURE_PROXY_SSL_HEADER)


class ProductionStaticUrlTests(SimpleTestCase):
    """Load real production settings in a subprocess (they resolve at import)."""

    SCRIPT = '\n'.join((
        'import json, requests',
        # No EC2 metadata service locally; fail the IMDS probe immediately.
        'def _no_imds(*args, **kwargs):',
        '    raise requests.ConnectionError("no IMDS in tests")',
        'requests.get = requests.put = _no_imds',
        'import django',
        'django.setup()',
        'from django.conf import settings',
        'from django.templatetags.static import static',
        'from django.core.files.storage import default_storage',
        'print(json.dumps({',
        '    "STATIC_URL": settings.STATIC_URL,',
        '    "MEDIA_URL": settings.MEDIA_URL,',
        '    "css": static("css/new.css"),',
        '    "media": default_storage.url("task_images/x.png"),',
        '}))',
    ))

    def test_prod_static_and_media_urls_are_root_relative(self):
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(('RDS_', 'DJANGO_STATIC_URL', 'STATIC_RELEASE_ID'))
        }
        env.update({
            'DJANGO_SETTINGS_MODULE': 'interoves_django.settings',
            'IS_PROD': 'TRUE',
            'USE_S3': 'TRUE',
            'AWS_STORAGE_BUCKET_NAME': 'interoves-test-bucket',
            'STATIC_RELEASE_ID': 'r-test',
            'DJANGO_SECRET_KEY': 'test-only',
            'AWS_EC2_METADATA_DISABLED': 'true',
        })
        result = subprocess.run(
            [sys.executable, '-c', self.SCRIPT],
            cwd=settings.BASE_DIR, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr[-4000:])
        urls = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(urls['STATIC_URL'], '/static/releases/r-test/')
        self.assertEqual(urls['css'], '/static/releases/r-test/css/new.css')
        self.assertEqual(urls['MEDIA_URL'], '/media/')
        self.assertEqual(urls['media'], '/media/task_images/x.png')


@override_settings(STATIC_URL=PROD_LIKE_STATIC_URL)
class RenderedPageAssetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_login_modal_deps()
        cls.user = User.objects.create_user('alt-host-assets', password='pw')

    def _home(self, host, *, login=False):
        client = Client()
        if login:
            client.force_login(self.user)
        response = client.get('/', HTTP_HOST=host)
        self.assertEqual(response.status_code, 200, host)
        return response.content.decode()

    def _pages(self):
        for host in HOSTS:
            for login in (False, True):
                yield host, login, self._home(host, login=login)

    def test_home_page_returns_200_on_both_hosts(self):
        for host in HOSTS:
            self._home(host)

    def test_assets_are_root_relative_or_known_integrations(self):
        for host, login, html in self._pages():
            for tag, url in _collect(html).assets:
                with self.subTest(host=host, login=login, tag=tag, url=url):
                    parts = urlsplit(url)
                    if not parts.netloc:
                        self.assertTrue(url.startswith('/'), url)
                        continue
                    self.assertIn(parts.hostname, EXTERNAL_INTEGRATION_HOSTS)

    def test_page_does_not_name_the_main_host_for_internal_resources(self):
        html = self._home(RU_HOST, login=True)
        self.assertNotRegex(html, r'(https?:)?//(www\.)?interoves\.com/(static|media|api|ws)/')
        for marker in ('cdn.jsdelivr.net', 'unpkg.com', 'cdnjs.cloudflare.com',
                       'fonts.googleapis.com', 'fonts.gstatic.com'):
            self.assertNotIn(marker, html)

    def test_rendered_static_urls_resolve_to_shipped_files(self):
        html = self._home(RU_HOST)
        for _tag, url in _collect(html).assets:
            if urlsplit(url).path.startswith(PROD_LIKE_STATIC_URL):
                with self.subTest(url=url):
                    self.assertIsNotNone(_local_static_path(url), url)

    def test_linked_stylesheets_load_only_local_fonts_and_images(self):
        stylesheets = _collect(self._home(RU_HOST)).stylesheets
        self.assertTrue(any('phosphor-icons-web/2.1.2/regular/style.css' in s for s in stylesheets))
        self.assertTrue(any('phosphor-icons-web/2.1.2/fill/style.css' in s for s in stylesheets))
        for href in stylesheets:
            css_path = _local_static_path(href)
            self.assertIsNotNone(css_path, href)
            css = Path(css_path).read_text(encoding='utf-8')
            refs = [m.group(2) for m in CSS_URL_RE.finditer(css)]
            refs += CSS_IMPORT_RE.findall(css)
            for ref in refs:
                if ref.startswith('data:') or ref.startswith('#'):
                    continue
                with self.subTest(stylesheet=href, ref=ref):
                    self.assertNotRegex(ref, r'^([a-z]+:)?//', 'CSS must not load remote files')
                    target = urlsplit(ref).path
                    if target.startswith('/'):
                        self.assertIsNotNone(_local_static_path(target), ref)
                    else:
                        self.assertTrue((Path(css_path).parent / target).resolve().is_file(), ref)


class OAuthCallbackHostTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_login_modal_deps()

    @override_settings(ACCOUNT_DEFAULT_HTTP_PROTOCOL='https')
    def test_oauth_redirect_uri_follows_the_request_host(self):
        # Each provider must therefore register both callback URLs.
        for host in HOSTS:
            with self.subTest(host=host):
                response = Client().get('/accounts/yandex/login/', HTTP_HOST=host)
                self.assertEqual(response.status_code, 302)
                query = parse_qs(urlsplit(response['Location']).query)
                self.assertEqual(
                    query['redirect_uri'],
                    [f'https://{host}/accounts/yandex/login/callback/'],
                )


class AlternateHostSessionAndCsrfTests(TestCase):
    """The origin sees HTTP; browsers send Origin: https://<host>."""

    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        CheckerType.objects.get_or_create(pk='replacements_lines')
        _ensure_login_modal_deps()
        for name in ('Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        now = timezone.now()
        cls.game = Game.objects.create(
            id='alt_host_game',
            name='Alternate host',
            author='test',
            is_ready=True,
            is_playable=True,
            is_tournament=False,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
        )
        group = TaskGroup.objects.create(label='Alternate host replacements')
        GameTaskGroup.objects.create(game=cls.game, task_group=group, number=1)
        with patch('games.realtime.track_compat.track_task_change'):
            cls.task = Task.objects.create(
                task_group=group,
                number='1',
                task_type='replacements_lines',
                text='FOO\nBAR',
                checker_id='replacements_lines',
                checker_data=json.dumps({'lines': [['FOO'], ['BAR']]}),
                points=2,
            )
        team = Team.objects.create(name='alt_host_team')
        cls.user = User.objects.create_user('alt-host-player', password='pw')
        Profile.objects.create(user=cls.user, team_on=team)

    def _player(self, host):
        client = Client(enforce_csrf_checks=True, HTTP_HOST=host)
        client.force_login(self.user)
        page = client.get(f'/games/{self.game.pk}/1/')
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn(f'data-track-url="/games/{self.game.pk}/track"', html)
        self.assertNotRegex(html, r'(https?:)?//(www\.)?interoves\.com/send_attempt')
        return client, client.cookies['csrftoken'].value

    def _send(self, client, token, origin, line_index=0, answer='FOO'):
        with patch('games.site.views.game.attempt.track_actor_task_change'):
            return client.post(
                f'/send_attempt/{self.task.pk}/',
                {'game_id': self.game.pk, 'line_index': str(line_index), 'answers[]': answer},
                HTTP_X_REQUESTED_WITH='XMLHttpRequest',
                HTTP_X_CSRFTOKEN=token,
                HTTP_ORIGIN=origin,
            )

    def test_game_attempt_is_accepted_from_each_hosts_own_origin(self):
        # One team: a distinct line per host, so neither send is a duplicate.
        for line_index, (host, answer) in enumerate(zip(HOSTS, ('FOO', 'BAR'))):
            with self.subTest(host=host):
                client, token = self._player(host)
                response = self._send(client, token, f'https://{host}', line_index, answer)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['status'], 'ok')

    def test_foreign_origin_is_rejected(self):
        client, token = self._player(RU_HOST)
        response = self._send(client, token, 'https://evil.example')
        self.assertEqual(response.status_code, 403)

    def test_session_survives_requests_and_logout_needs_csrf(self):
        for host in HOSTS:
            with self.subTest(host=host):
                client, token = self._player(host)
                home = client.get('/')
                self.assertContains(home, 'data-authenticated="1"')
                rejected = client.post('/logout/', HTTP_ORIGIN=f'https://{host}')
                self.assertEqual(rejected.status_code, 403)
                logout = client.post(
                    '/logout/', {'csrfmiddlewaretoken': token}, HTTP_ORIGIN=f'https://{host}',
                )
                self.assertEqual(logout.status_code, 302)
                self.assertContains(client.get('/'), 'data-authenticated="0"')


class TrackSocketHostJsTests(SimpleTestCase):
    def test_socket_urls_follow_the_page_host(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('node is not installed')
        script = Path(settings.BASE_DIR) / 'static' / 'js' / 'track_ws_host.test.js'
        result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, msg=result.stdout[-4000:] + result.stderr[-4000:])
        self.assertIn('track_ws host tests ok', result.stdout)
