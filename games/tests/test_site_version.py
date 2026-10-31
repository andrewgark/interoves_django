"""interoves.com / ru.interoves.com: visitor country, the hint and the switcher."""
import gzip
import io
import os
import tempfile
import time
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from games.site.http import site_version, visitor_country
from games.tests.test_yandex_auth import _ensure_login_modal_deps

INTL_HOST = 'interoves.com'
RU_HOST = 'ru.interoves.com'
CF_EDGE = '172.70.1.2'
ALB = '172.31.20.5'
GATEWAY = '3.120.1.1'
UVDS_TAILSCALE = '100.88.1.2'
RU_CLIENT = '95.165.1.1'
DE_CLIENT = '91.198.174.192'


def _request(host, xff='', path='/', remote='127.0.0.1', **extra):
    meta = {'HTTP_HOST': host, 'REMOTE_ADDR': remote, **extra}
    if xff:
        meta['HTTP_X_FORWARDED_FOR'] = xff
    return RequestFactory().get(path, **meta)


def _geo(table):
    return patch.object(visitor_country, 'lookup_country', side_effect=lambda addr: table.get(str(addr), 'unknown'))


GEO = {RU_CLIENT: 'RU', DE_CLIENT: 'DE'}


class VisitorCountryTests(SimpleTestCase):
    def country(self, *args, **kwargs):
        with _geo(GEO):
            return visitor_country.visitor_country(_request(*args, **kwargs))

    def test_intl_trusts_cf_country_behind_cloudflare_and_alb(self):
        xff = f'{DE_CLIENT}, {CF_EDGE}, {ALB}'
        self.assertEqual(self.country(INTL_HOST, xff, HTTP_CF_IPCOUNTRY='RU'), 'RU')

    def test_intl_falls_back_to_geoip_of_the_client_cloudflare_saw(self):
        xff = f'1.1.1.1, {RU_CLIENT}, {CF_EDGE}, {ALB}'
        self.assertEqual(self.country(INTL_HOST, xff), 'RU')
        self.assertEqual(self.country(INTL_HOST, xff, HTTP_CF_IPCOUNTRY='XX'), 'RU')

    def test_intl_cf_country_does_not_need_a_public_client_hop(self):
        # Cloudflare Pseudo IPv4 puts a 240.0.0.0/4 placeholder in the header.
        xff = f'240.1.2.3, {CF_EDGE}, {ALB}'
        self.assertEqual(self.country(INTL_HOST, xff, HTTP_CF_IPCOUNTRY='RU'), 'RU')
        self.assertEqual(self.country(INTL_HOST, xff), 'unknown')

    def test_intl_ignores_cf_country_not_relayed_by_cloudflare(self):
        # Straight to the ALB :443 listener, or through UltraVDS with a forged Host.
        self.assertEqual(self.country(INTL_HOST, f'{RU_CLIENT}, {GATEWAY}, {ALB}', HTTP_CF_IPCOUNTRY='RU'), 'unknown')
        self.assertEqual(self.country(INTL_HOST, f'{RU_CLIENT}, {UVDS_TAILSCALE}', HTTP_CF_IPCOUNTRY='RU'), 'unknown')
        self.assertEqual(self.country(INTL_HOST, '', HTTP_CF_IPCOUNTRY='RU'), 'unknown')

    def test_ru_via_gateway_and_alb(self):
        self.assertEqual(self.country(RU_HOST, f'{RU_CLIENT}, {GATEWAY}, {ALB}'), 'RU')
        self.assertEqual(self.country(RU_HOST, f'{DE_CLIENT}, {GATEWAY}, {ALB}'), 'DE')

    def test_ru_via_fallback_tailscale_node(self):
        self.assertEqual(self.country(RU_HOST, f'{DE_CLIENT}, {UVDS_TAILSCALE}'), 'DE')

    def test_ru_rejects_unknown_chain_shapes(self):
        # A client-supplied hop survives only if a proxy appended to it.
        self.assertEqual(self.country(RU_HOST, f'{RU_CLIENT}, {DE_CLIENT}, {GATEWAY}, {ALB}'), 'unknown')
        self.assertEqual(self.country(RU_HOST, f'{RU_CLIENT}, {ALB}'), 'unknown')
        self.assertEqual(self.country(RU_HOST, RU_CLIENT), 'unknown')
        # Never take a proxy or private address for the visitor.
        self.assertEqual(self.country(RU_HOST, f'10.0.0.1, {GATEWAY}, {ALB}'), 'unknown')
        self.assertEqual(self.country(RU_HOST, f'{UVDS_TAILSCALE}, {GATEWAY}, {ALB}'), 'unknown')
        self.assertEqual(self.country(RU_HOST, f'garbage, {GATEWAY}, {ALB}'), 'unknown')

    def test_requires_the_local_nginx_as_peer(self):
        self.assertEqual(self.country(RU_HOST, f'{RU_CLIENT}, {GATEWAY}, {ALB}', remote=GATEWAY), 'unknown')

    def test_other_hosts_are_unknown(self):
        self.assertEqual(self.country('localhost', f'{RU_CLIENT}, {GATEWAY}, {ALB}'), 'unknown')

    @override_settings(GEOIP_COUNTRY_DB='/nonexistent/geoip.mmdb')
    def test_missing_database_is_unknown(self):
        self.assertEqual(visitor_country.lookup_country('95.165.1.1'), 'unknown')


class AlternateUrlTests(SimpleTestCase):
    def url(self, path, version='ru', host=INTL_HOST):
        return site_version.alternate_url(_request(host, path=path), version)

    def test_keeps_path_and_ordinary_query(self):
        self.assertEqual(self.url('/ladder/'), 'https://ru.interoves.com/ladder/')
        self.assertEqual(self.url('/salad/?date=2026-10-31&utm_source=tg', 'intl', RU_HOST),
                         'https://interoves.com/salad/?date=2026-10-31&utm_source=tg')

    def test_drops_sensitive_query_parameters(self):
        self.assertEqual(
            self.url('/ladder/?code=abc&state=x&access_token=t&payment_id=1&next=//evil.example/&tab=rules'),
            'https://ru.interoves.com/ladder/?tab=rules',
        )

    def test_sensitive_routes_go_home(self):
        for path in (
            '/accounts/google/login/callback/?code=abc&state=x',
            '/accounts/telegram/callback/',
            '/telegram/interoves-telegram/login/',
            '/pay/?payment=return',
            '/subscription/?payment=gift-return',
            '/subscription/gift/7/reveal/',
            '/donate/status/secret-token/',
            '/team/invite/abc123/',
            '/meta100/team/invite/abc123/accept/',
            '/profile/merge/',
            '/logout/',
        ):
            with self.subTest(path=path):
                self.assertEqual(self.url(path), 'https://ru.interoves.com/')

    def test_drops_payment_return_markers(self):
        self.assertEqual(
            self.url('/vote/next-game/2026/september/?from=tribute_return&candidate=a&from_tab=return-policy'),
            'https://ru.interoves.com/vote/next-game/2026/september/?candidate=a&from_tab=return-policy',
        )

    def test_never_leaves_the_allowlisted_hosts(self):
        for path in ('//evil.example/', '/\\evil.example/', '/%2F%2Fevil.example'):
            with self.subTest(path=path):
                self.assertTrue(self.url(path).startswith('https://ru.interoves.com/'))


class SuggestionTests(SimpleTestCase):
    def suggest(self, host, client_ip, path='/', cookie=None):
        xff = f'{client_ip}, {GATEWAY}, {ALB}' if host == RU_HOST else f'{client_ip}, {CF_EDGE}, {ALB}'
        request = _request(host, xff, path=path)
        if cookie is not None:
            request.COOKIES[site_version.COOKIE_NAME] = cookie
        with _geo({**GEO, '8.8.8.8': 'unknown'}):
            return site_version.suggested_version(request)

    def test_decision_table(self):
        self.assertEqual(self.suggest(INTL_HOST, RU_CLIENT), 'ru')
        self.assertIsNone(self.suggest(INTL_HOST, DE_CLIENT))
        self.assertIsNone(self.suggest(RU_HOST, RU_CLIENT))
        self.assertEqual(self.suggest(RU_HOST, DE_CLIENT), 'intl')
        self.assertIsNone(self.suggest(INTL_HOST, '8.8.8.8'))
        self.assertIsNone(self.suggest(RU_HOST, '8.8.8.8'))

    def test_not_on_sensitive_routes(self):
        self.assertIsNone(self.suggest(INTL_HOST, RU_CLIENT, path='/accounts/yandex/login/callback/?code=1'))
        self.assertIsNone(self.suggest(INTL_HOST, RU_CLIENT, path='/pay/?payment=return'))
        self.assertIsNone(self.suggest(INTL_HOST, RU_CLIENT, path='/subscription/?payment=return'))
        self.assertIsNone(self.suggest(INTL_HOST, RU_CLIENT, path='/vote/next-game/2026/september/?from=tribute_return'))
        self.assertIsNone(self.suggest(INTL_HOST, RU_CLIENT, path='/ladder/?code=1'))

    def test_saved_decision_silences_the_hint(self):
        now = int(time.time())
        self.assertIsNone(self.suggest(INTL_HOST, RU_CLIENT, cookie=f'stayed:intl:{now - 86400}'))
        self.assertIsNone(self.suggest(RU_HOST, DE_CLIENT, cookie=f'chosen:ru:{now - 200 * 86400}'))
        # The browser's clock may run ahead of the server's.
        self.assertIsNone(self.suggest(INTL_HOST, RU_CLIENT, cookie=f'stayed:intl:{now + 3600}'))
        # "Stay here" expires after 30 days; garbage is ignored.
        self.assertEqual(self.suggest(INTL_HOST, RU_CLIENT, cookie=f'stayed:intl:{now - 31 * 86400}'), 'ru')
        self.assertEqual(self.suggest(INTL_HOST, RU_CLIENT, cookie='sessionid-like-junk'), 'ru')


class ContextTests(SimpleTestCase):
    def test_computed_once_per_request(self):
        request = _request(INTL_HOST, f'{RU_CLIENT}, {CF_EDGE}, {ALB}', HTTP_CF_IPCOUNTRY='RU')
        with patch.object(site_version, 'alternate_url', wraps=site_version.alternate_url) as built:
            first = site_version.site_version(request)
            calls = built.call_count
            self.assertIs(site_version.site_version(request), first)
        self.assertEqual(built.call_count, calls)
        self.assertEqual(first['site_version']['suggest'], 'ru')


class SiteVersionPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_login_modal_deps()

    def get(self, host, xff, **extra):
        with _geo(GEO):
            return Client().get('/about/', HTTP_HOST=host, HTTP_X_FORWARDED_FOR=xff, **extra)

    def test_menu_offers_both_versions_and_marks_the_current_one(self):
        response = self.get(RU_HOST, f'{RU_CLIENT}, {GATEWAY}, {ALB}')
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="https://interoves.com/about/" data-site-version-choice="intl"', html)
        self.assertIn('aria-current="true"', html)
        self.assertIn('ru.interoves.com', html)
        self.assertNotIn('data-site-version-hint=', html)

    def test_russian_visitor_on_intl_gets_the_hint(self):
        response = self.get(INTL_HOST, f'{RU_CLIENT}, {CF_EDGE}, {ALB}', HTTP_CF_IPCOUNTRY='RU')
        html = response.content.decode()
        self.assertIn('data-site-version-hint="ru"', html)
        self.assertIn('Вы в России? Есть альтернативная версия сайта', html)
        self.assertIn('href="https://ru.interoves.com/about/" data-site-version-choice="ru"', html)

    def test_foreign_visitor_on_ru_gets_the_hint(self):
        html = self.get(RU_HOST, f'{DE_CLIENT}, {GATEWAY}, {ALB}').content.decode()
        self.assertIn('data-site-version-hint="intl"', html)
        self.assertIn('Доступна международная версия', html)


class SyncGeoipDbTests(SimpleTestCase):
    def test_bad_download_keeps_the_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'country.mmdb')
            with open(path, 'wb') as fh:
                fh.write(b'old')
            os.utime(path, (0, 0))
            body = gzip.compress(b'not an mmdb')
            with override_settings(GEOIP_COUNTRY_DB=path), \
                    patch('urllib.request.urlopen', side_effect=lambda *a, **k: io.BytesIO(body)), \
                    self.assertRaises(CommandError):
                call_command('sync_geoip_db', stdout=io.StringIO())
            with open(path, 'rb') as fh:
                self.assertEqual(fh.read(), b'old')
            self.assertEqual(sorted(os.listdir(directory)), ['country.mmdb'])

    def test_fresh_file_is_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'country.mmdb')
            open(path, 'wb').close()
            with override_settings(GEOIP_COUNTRY_DB=path), patch('urllib.request.urlopen') as urlopen:
                call_command('sync_geoip_db', stdout=io.StringIO())
            urlopen.assert_not_called()
