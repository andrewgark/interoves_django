"""Tests for slow-request logging middleware."""

from django.test import RequestFactory, TestCase

from games.middleware.request_timing import RequestTimingMiddleware


class RequestTimingMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = RequestTimingMiddleware(
            lambda request: type('R', (), {'status_code': 200})(),
        )

    def test_logs_slow_send_attempt(self):
        request = self.factory.post('/send_attempt/1/')
        self.middleware.slow_ms = 0
        with self.assertLogs('interoves.request_timing', level='WARNING') as cm:
            self.middleware(request)
        self.assertTrue(any('slow_request' in line for line in cm.output))

    def test_ignores_unrelated_paths(self):
        request = self.factory.get('/alphabetty/1/')
        with self.assertNoLogs('interoves.request_timing', level='WARNING'):
            self.middleware(request)

    def test_watches_alphabetty_guess(self):
        request = self.factory.post('/alphabetty/3/guess/')
        self.middleware.slow_ms = 0
        with self.assertLogs('interoves.request_timing', level='WARNING'):
            self.middleware(request)
