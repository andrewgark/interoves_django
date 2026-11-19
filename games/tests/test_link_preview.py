from django.test import Client, RequestFactory, SimpleTestCase, TestCase

from games.community.preview import HUB_TITLE, page_og


class PageOgTests(SimpleTestCase):
    def og(self, path):
        return page_og(RequestFactory().get(path, HTTP_HOST='interoves.com'))

    def test_hub(self):
        og = self.og('/')
        self.assertEqual(og['title'], HUB_TITLE)
        self.assertIn('Лесенка', og['description'])
        self.assertTrue(og['image'].startswith('https://interoves.com/'))

    def test_daily_archive_uses_plural_name(self):
        og = self.og('/ladder/')
        self.assertEqual(og['title'], 'Лесенки · Inter Oves')
        self.assertIn('цепочку слов', og['description'])
        self.assertIn('каждый день', og['description'])

    def test_numbered_edition_is_named_not_described(self):
        self.assertEqual(self.og('/salad/123/')['title'], 'Салатик №123 · Inter Oves')
        self.assertEqual(self.og('/censorly/45/')['title'], 'Цензурка №45 · Inter Oves')

    def test_entry_and_share_links_use_singular_name(self):
        self.assertEqual(self.og('/alphabetty/first/')['title'], 'Алфавитка · Inter Oves')
        self.assertEqual(self.og('/censorly/r/abc123/')['title'], 'Цензурка · Inter Oves')
        self.assertEqual(self.og('/salad/0123456789abcdef/')['title'], 'Салатик · Inter Oves')

    def test_other_pages_fall_back_to_page_title(self):
        og = self.og('/profile/')
        self.assertEqual(og['title'], '')
        self.assertEqual(og['url'], 'https://interoves.com/profile/')


class BaseTemplateOgTests(TestCase):
    def setUp(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        site = Site.objects.get_current()
        for provider in ('google', 'yandex', 'vk'):
            app, _ = SocialApp.objects.get_or_create(
                provider=provider, defaults={'name': provider, 'client_id': 't', 'secret': 't'},
            )
            app.sites.add(site)

    def test_hub_renders_preview_once(self):
        body = Client().get('/').content.decode()
        self.assertEqual(body.count('property="og:title"'), 1)
        self.assertIn('<meta property="og:title" content="{}">'.format(HUB_TITLE), body)
        self.assertIn('og-1200x630.png', body)

    def test_hand_written_preview_is_not_duplicated(self):
        body = Client().get('/about/').content.decode()
        self.assertEqual(body.count('property="og:title"'), 1)
        self.assertEqual(body.count('name="description"'), 1)
        self.assertIn('<meta property="og:title" content="О нас · Inter Oves">', body)
        self.assertIn('og-1200x630.png', body)
