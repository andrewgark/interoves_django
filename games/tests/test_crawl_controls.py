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
