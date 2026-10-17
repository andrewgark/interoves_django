"""Tests for slow-request logging middleware."""

from django.test import RequestFactory, TestCase
from django.urls import resolve

from games.middleware.request_timing import RequestTimingMiddleware, _watch_path, timing_phase


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

    def test_logs_slow_unrelated_paths_too(self):
        request = self.factory.get('/alphabetty/1/')
        request.resolver_match = resolve(request.path)
        self.middleware.slow_ms = 0
        with self.assertLogs('interoves.request_timing', level='WARNING') as cm:
            self.middleware(request)
        log = '\n'.join(cm.output)
        self.assertIn('endpoint=alphabetty/<str:number>/', log)
        self.assertIn('pid=', log)

    def test_watches_alphabetty_guess(self):
        request = self.factory.post('/alphabetty/3/guess/')
        self.middleware.slow_ms = 0
        with self.assertLogs('interoves.request_timing', level='WARNING'):
            self.middleware(request)

    def test_watches_raddle_ui_and_daily_timing(self):
        for path in ('/send_raddle_ui/6831/', '/ladder/88/timing/', '/replacements/170/timing/'):
            request = self.factory.post(path)
            self.middleware.slow_ms = 0
            with self.assertLogs('interoves.request_timing', level='WARNING'):
                self.middleware(request)

    def test_watches_censorly_guess(self):
        for path in ('/censorly/12/guess/', '/censorly/r/abcd1234/guess/'):
            request = self.factory.post(path)
            self.middleware.slow_ms = 0
            with self.assertLogs('interoves.request_timing', level='WARNING'):
                self.middleware(request)

    def test_watches_censorly_play_pages_but_not_nested_state_routes(self):
        self.assertTrue(_watch_path('/censorly/5/'))
        self.assertTrue(_watch_path('/censorly/r/abcd1234/'))
        self.assertFalse(_watch_path('/censorly/5/state/'))

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

    def test_slow_request_log_has_correlation_and_process_context_without_query(self):
        request = self.factory.get(
            '/censorly/r/abcd1234abcd1234/state/?code=secret-code'
        )
        request.resolver_match = resolve(request.path)
        request.interoves_request_id = 'safe-request-id'
        self.middleware.slow_ms = 0
        with self.assertLogs('interoves.request_timing', level='WARNING') as cm:
            self.middleware(request)
        log = '\n'.join(cm.output)
        self.assertIn('request_id=safe-request-id', log)
        self.assertIn('endpoint=censorly/r/<str:share_hash>/state/', log)
        self.assertNotIn('abcd1234abcd1234', log)
        self.assertNotIn('secret-code', log)

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
