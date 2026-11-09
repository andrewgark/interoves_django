from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings


@override_settings(DEBUG=False)
class ErrorPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # base.html renders the social login buttons.
        site, _ = Site.objects.get_or_create(id=1, defaults={'domain': 'testserver', 'name': 'test'})
        for provider, name in (('google', 'Google'), ('vk', 'VK'), ('yandex', 'Yandex')):
            app, _ = SocialApp.objects.get_or_create(
                provider=provider, defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
            )
            app.sites.add(site)

    def test_missing_page_is_in_russian_with_the_site_around_it(self):
        response = self.client.get('/no-such-page-at-all/')
        self.assertEqual(response.status_code, 404)
        body = response.content.decode()
        self.assertIn('Такой страницы нет', body)
        self.assertIn('href="/salad/last/"', body)
        self.assertIn('name="viewport"', body)
        self.assertNotIn('The requested resource was not found', body)

    def test_server_error_page_renders_without_context(self):
        from django.template.loader import render_to_string
        html = render_to_string('500.html')
        self.assertIn('Что-то сломалось', html)
        self.assertIn('name="viewport"', html)
