import re

from django.test import TestCase

from games.tests.test_yandex_auth import _ensure_login_modal_deps


class CrawlControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_login_modal_deps()

    def test_robots_txt_keeps_crawlers_off_login_and_results_variants(self):
        response = self.client.get('/robots.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain; charset=utf-8')
        body = response.content.decode()
        self.assertIn('Disallow: /accounts/', body)
        self.assertIn('Disallow: /*?*snapshot=', body)

    def test_login_links_are_nofollow(self):
        html = self.client.get('/').content.decode()
        links = re.findall(r'<a [^>]*href="/accounts/[a-z]+/login/[^>]*>', html)
        self.assertTrue(links)
        for link in links:
            self.assertIn('rel="nofollow"', link)

    def test_query_variants_are_noindex(self):
        plain = self.client.get('/').content.decode()
        self.assertNotIn('content="noindex,nofollow"', plain)
        variant = self.client.get('/?sort=time').content.decode()
        self.assertIn('<meta name="robots" content="noindex,nofollow">', variant)


class AuthNextTests(TestCase):
    def _auth_next(self, path):
        from django.template import Context, Template
        from django.test import RequestFactory

        request = RequestFactory().get(path)
        return Template('{% load filters %}{% auth_next %}').render(Context({'request': request}))

    def test_results_view_state_is_not_carried_into_login_links(self):
        self.assertEqual(
            self._auth_next(
                '/censorly/results/?month=2026-11&snapshot=abc&sort=time&actors=user&page=3',
            ),
            '/censorly/results/?month=2026-11',
        )
        self.assertEqual(self._auth_next('/ladder/results/?snapshot=abc'), '/ladder/results/')


class CrawlProbeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_login_modal_deps()

    def test_logs_only_crawl_space_requests(self):
        with self.assertLogs('games.crawl_probe', level='INFO') as logs:
            self.client.get(
                '/?snapshot=abc', HTTP_USER_AGENT='TestBot/1.0', HTTP_X_FORWARDED_FOR='203.0.113.7',
            )
        self.assertIn('"ua":"TestBot/1.0"', logs.output[0])
        self.assertIn('"ip":"203.0.113.7"', logs.output[0])
        with self.assertNoLogs('games.crawl_probe', level='INFO'):
            self.client.get('/')
