"""Tests for slow-request logging middleware."""

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.db import connection
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve

from games.site.http.middleware.request_timing import RequestTimingMiddleware, _watch_path, timing_phase


class RequestTimingMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = RequestTimingMiddleware(
            lambda request: type('R', (), {'status_code': 200})(),
        )

    def test_logger_does_not_repeat_through_root(self):
        # Daphne gives the root logger its own stderr handler.
        from django.conf import settings
        loggers = settings.LOGGING['loggers']
        for name in ('interoves.request_timing', 'application', 'daphne'):
            self.assertIs(loggers[name].get('propagate'), False, name)

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

    def test_counts_sql_on_watched_paths_only(self):
        def response(_request):
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
            return type('R', (), {'status_code': 200})()

        middleware = RequestTimingMiddleware(response)
        middleware.slow_ms = 0
        with self.assertLogs('interoves.request_timing', level='WARNING') as cm:
            middleware(self.factory.post('/send_attempt/1/'))
            middleware(self.factory.get('/alphabetty/1/'))
        watched, unwatched = cm.output
        self.assertIn('sql_count=1 sql_ms=', watched)
        self.assertIn('sql_count=- sql_ms=-', unwatched)

    @override_settings(REQUEST_TIMING_SLOW_MS=0)
    def test_hub_logs_its_phases(self):
        self.assertTrue(_watch_path('/'))
        site, _ = Site.objects.get_or_create(id=1, defaults={'domain': 'testserver', 'name': 'test'})
        for provider in ('google', 'vk', 'yandex', 'telegram'):
            app, _ = SocialApp.objects.get_or_create(
                provider=provider,
                defaults={'name': provider, 'client_id': 'test', 'secret': 'test'},
            )
            app.sites.add(site)
        with self.assertLogs('interoves.request_timing', level='WARNING') as cm:
            response = Client().get('/')
        self.assertEqual(response.status_code, 200)
        log = '\n'.join(cm.output)
        for phase in (
            'hub_section_games', 'hub_daily_cards', 'hub_next_transition',
            'hub_desyatochki', 'hub_onboarding', 'hub_games_list', 'hub_render',
        ):
            self.assertIn(phase + ':', log)
        self.assertRegex(log, r'sql_count=[1-9]')

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
