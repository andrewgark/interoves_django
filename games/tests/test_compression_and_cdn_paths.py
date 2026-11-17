from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase

from games.ops.analytics.identity import should_skip_anonymous_identity
from games.site.http.middleware.compression import TextGZipMiddleware

BODY = b'<html>' + b'interoves ' * 200 + b'</html>'


def _run(response, *, accept='gzip, br'):
    request = RequestFactory().get('/', HTTP_ACCEPT_ENCODING=accept)
    return TextGZipMiddleware(lambda _request: response)(request)


class TextGZipMiddlewareTests(SimpleTestCase):
    def test_html_is_gzipped(self):
        response = _run(HttpResponse(BODY, content_type='text/html; charset=utf-8'))
        self.assertEqual(response['Content-Encoding'], 'gzip')
        self.assertLess(len(response.content), len(BODY))
        self.assertIn('Accept-Encoding', response['Vary'])

    def test_json_is_gzipped(self):
        response = _run(HttpResponse(BODY, content_type='application/json'))
        self.assertEqual(response['Content-Encoding'], 'gzip')

    def test_client_without_gzip_gets_plain_body(self):
        response = _run(HttpResponse(BODY, content_type='text/html'), accept='identity')
        self.assertFalse(response.has_header('Content-Encoding'))
        self.assertEqual(response.content, BODY)

    def test_binary_types_pass_through(self):
        for content_type in ('application/pdf', 'image/png', 'application/octet-stream'):
            with self.subTest(content_type=content_type):
                response = _run(HttpResponse(BODY, content_type=content_type))
                self.assertFalse(response.has_header('Content-Encoding'))
                self.assertEqual(response.content, BODY)

    def test_event_stream_passes_through(self):
        response = _run(StreamingHttpResponse(iter([BODY]), content_type='text/event-stream'))
        self.assertFalse(response.has_header('Content-Encoding'))


class AnonymousIdentitySkipTests(SimpleTestCase):
    def _skip(self, path):
        return should_skip_anonymous_identity(RequestFactory().get(path))

    def test_booklet_files_get_no_identity_cookies(self):
        for path in (
            '/eurovision_booklet/2026/pdf/eurovision2026_ru.pdf',
            '/eurovision_booklet/2026asia/pdf/booklet.pdf',
            '/eurovision_booklet/assets/flags/se.svg',
            '/eurovision_booklet/editions/eurovision-asia-2026/assets/x.png',
        ):
            with self.subTest(path=path):
                self.assertTrue(self._skip(path))

    def test_booklet_pages_still_get_identity(self):
        for path in ('/eurovision_booklet/', '/eurovision_booklet/2026/', '/eurovision_booklet/2026asia/'):
            with self.subTest(path=path):
                self.assertFalse(self._skip(path))
