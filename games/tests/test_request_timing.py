"""Tests for slow-request logging middleware."""

from django.test import RequestFactory, TestCase

from games.middleware.request_timing import RequestTimingMiddleware, timing_phase


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

    def test_logs_named_phases_without_request_payload(self):
        def response(request):
            with timing_phase(request, 'check_attempt'):
                pass
            return type('R', (), {'status_code': 200})()

        middleware = RequestTimingMiddleware(response)
        middleware.slow_ms = 0
        request = self.factory.post('/send_attempt/1/', {'text': 'secret answer'})
        with self.assertLogs('interoves.request_timing', level='WARNING') as cm:
            middleware(request)
        log = '\n'.join(cm.output)
        self.assertIn('phases=check_attempt:', log)
        self.assertNotIn('secret answer', log)

    def test_logs_slow_exception(self):
        def response(_request):
            raise RuntimeError('boom')

        middleware = RequestTimingMiddleware(response)
        middleware.slow_ms = 0
        request = self.factory.post('/send_attempt/1/')
        with self.assertLogs('interoves.request_timing', level='WARNING') as cm:
            with self.assertRaises(RuntimeError):
                middleware(request)
        self.assertIn('status=exception', '\n'.join(cm.output))
