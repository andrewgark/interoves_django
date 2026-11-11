from django.test import TestCase, override_settings


@override_settings(DEBUG=False)
class ErrorPageTests(TestCase):
    def test_missing_page_is_in_russian_with_the_site_around_it(self):
        response = self.client.get('/no-such-page-at-all/')
        self.assertEqual(response.status_code, 404)
        body = response.content.decode()
        self.assertIn('Такой страницы нет', body)
        self.assertIn('href="/salad/last/"', body)
        self.assertIn('name="viewport"', body)
        self.assertNotIn('The requested resource was not found', body)
        # No social-login apps in tests: the page must not depend on them.

    def test_server_error_page_renders_without_context(self):
        from django.template.loader import render_to_string
        html = render_to_string('500.html')
        self.assertIn('Что-то сломалось', html)
        self.assertIn('name="viewport"', html)
