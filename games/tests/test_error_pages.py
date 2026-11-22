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

    def test_missing_page_does_not_run_context_processors(self):
        # A raising context processor made Django turn the 404 into a 500
        # with no traceback (/sitemap_index.xml/, Nov 21, 2026).
        with self.settings(TEMPLATES=_templates_with_processor(
            'games.tests.test_error_pages.raising_context_processor',
        )):
            response = self.client.get('/sitemap_index.xml/')
        self.assertEqual(response.status_code, 404)
        self.assertIn('Такой страницы нет', response.content.decode())


def raising_context_processor(request):
    raise RuntimeError('context processor failed')


def _templates_with_processor(path):
    import copy

    from django.conf import settings
    templates = copy.deepcopy(settings.TEMPLATES)
    templates[0]['OPTIONS']['context_processors'].append(path)
    return templates
