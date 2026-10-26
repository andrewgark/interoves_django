import json
import asyncio
import types
from unittest import TestCase
from unittest.mock import patch

from django.core.signals import got_request_exception
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.db.utils import OperationalError, ProgrammingError
from django.urls import path
from django.core.handlers.asgi import ASGIHandler

from games.site.http.middleware.db_transient_response import (
    TransientDatabaseResponseMiddleware,
    mysql_error_code,
)


def _session_probe_view(request):
    request.session.items()
    return HttpResponse('ok')


def _timing_db_error_view(request):
    request._interoves_timing_pre_mutation = True
    raise OperationalError(1040, 'Too many connections')


def _idempotent_timing_db_error_view(request):
    request._interoves_timing_event_idempotent = True
    raise OperationalError(2013, 'Lost connection during query')


def _legacy_timing_db_error_view(request):
    raise OperationalError(2013, 'Lost connection during query')


def _unsafe_post_db_error_view(request):
    raise OperationalError(2013, 'Lost connection during query')


_mutations = []
_asgi_started = None


def _mutate_then_db_error_view(request):
    _mutations.append('committed')
    raise OperationalError(1040, 'Too many connections after mutation')


def _session_save_after_mutation_view(request):
    request.session['mutation_marker'] = 'saved-by-view'
    return HttpResponse('mutation completed')


async def _asgi_ok_view(request):
    return HttpResponse('healthy')


async def _asgi_cancel_view(request):
    _asgi_started.set()
    await asyncio.Event().wait()


class _AsgiPreViewFailureMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/asgi-fail/':
            raise OperationalError(1040, 'Too many connections')
        return self.get_response(request)


urlpatterns = [
    path('session-probe/', _session_probe_view),
    path('ladder/10/timing/', _timing_db_error_view),
    path('ladder/11/timing/', _idempotent_timing_db_error_view),
    path('ladder/12/timing/', _legacy_timing_db_error_view),
    path('mutate/', _unsafe_post_db_error_view),
    path('mutate-then-db-error/', _mutate_then_db_error_view),
    path('session-save-after-mutation/', _session_save_after_mutation_view),
    path('asgi-ok/', _asgi_ok_view),
    path('asgi-cancel/', _asgi_cancel_view),
]


class SessionLoadFailureMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.session.items()
        return self.get_response(request)


def _signal_exception(request, exc):
    try:
        raise exc
    except Exception:
        got_request_exception.send(sender=None, request=request)


class TransientDatabaseResponseTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = TransientDatabaseResponseMiddleware(
            lambda request: HttpResponse('Internal Server Error', status=500)
        )

    def _response_after_error(self, request, exc):
        _signal_exception(request, exc)
        return self.middleware(request)

    def test_mysql_1040_during_pre_view_session_load_returns_retryable_503(self):
        request = self.factory.post('/ladder/10/timing/')
        request.interoves_request_id = 'request-1'
        response = self._response_after_error(
            request, OperationalError(1040, 'Too many connections'),
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Retry-After'], '2')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertFalse(json.loads(response.content)['ok'])

    def test_mysql_1040_during_timing_game_lookup_returns_503(self):
        request = self.factory.post('/ladder/10/timing/')
        request.resolver_match = object()  # URL resolution already happened.
        request._interoves_timing_pre_mutation = True
        response = self._response_after_error(
            request, OperationalError(1040, 'Too many connections'),
        )
        self.assertEqual(response.status_code, 503)

    def test_unknown_commit_outcome_only_returns_503_for_marked_idempotent_timing(self):
        request = self.factory.post('/ladder/10/timing/')
        request.resolver_match = object()
        request._interoves_timing_event_idempotent = True
        response = self._response_after_error(
            request, OperationalError(2013, 'Lost connection during query'),
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Retry-After'], '2')

    def test_unknown_commit_outcome_on_unmarked_post_keeps_django_500(self):
        request = self.factory.post('/payments/confirm/')
        request.resolver_match = object()
        response = self._response_after_error(
            request, OperationalError(2013, 'Lost connection during query'),
        )
        self.assertEqual(response.status_code, 500)

    def test_programming_exception_with_mysql_looking_argument_is_not_classified(self):
        self.assertIsNone(mysql_error_code(ValueError(1040, 'application bug')))

    def test_programming_error_is_not_reclassified_from_errno_or_prior_context(self):
        error = ProgrammingError(1040, 'invalid application query')
        error.__cause__ = OperationalError(1040, 'earlier connection failure')
        self.assertIsNone(mysql_error_code(error))

    def test_non_database_500_is_not_rewritten(self):
        request = self.factory.get('/ladder/10/results/')
        _signal_exception(request, ValueError('rendering bug'))
        response = self.middleware(request)
        self.assertEqual(response.status_code, 500)

    @override_settings(
        ROOT_URLCONF=__name__,
        MIDDLEWARE=[
            'games.middleware.auth_session_observability.RequestCorrelationMiddleware',
            'games.middleware.db_transient_response.TransientDatabaseResponseMiddleware',
        ],
    )
    def test_1040_after_view_started_and_mutated_is_not_retryable(self):
        _mutations.clear()
        response = Client(raise_request_exception=False).post('/mutate-then-db-error/')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(_mutations, ['committed'])

    @override_settings(
        ROOT_URLCONF=__name__,
        SESSION_ENGINE='django.contrib.sessions.backends.db',
        MIDDLEWARE=[
            'games.middleware.auth_session_observability.RequestCorrelationMiddleware',
            'games.middleware.db_transient_response.TransientDatabaseResponseMiddleware',
            'django.contrib.sessions.middleware.SessionMiddleware',
        ],
    )
    def test_1040_during_response_session_save_is_not_retryable(self):
        client = Client(raise_request_exception=False)
        with patch.object(SessionStore, 'save', side_effect=OperationalError(1040, 'Too many connections')):
            response = client.get('/session-save-after-mutation/')
        self.assertEqual(response.status_code, 500)

    def test_nested_non_database_exception_clears_previous_errno(self):
        request = self.factory.get('/ladder/10/results/')
        _signal_exception(request, OperationalError(1040, 'Too many connections'))
        _signal_exception(request, ValueError('later middleware defect'))
        response = self.middleware(request)
        self.assertEqual(response.status_code, 500)

    @staticmethod
    async def _asgi_call(app, path_info):
        sent = []
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message):
            sent.append(message)
        scope = {
            'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
            'method': 'GET', 'scheme': 'http', 'path': path_info,
            'raw_path': path_info.encode(), 'query_string': b'',
            'headers': [(b'host', b'testserver')], 'client': ('127.0.0.1', 1234),
            'server': ('testserver', 80),
        }
        await app(scope, receive, send)
        start = next(item for item in sent if item['type'] == 'http.response.start')
        body = b''.join(item.get('body', b'') for item in sent if item['type'] == 'http.response.body')
        return start['status'], body

    @override_settings(
        ROOT_URLCONF=__name__,
        MIDDLEWARE=[
            'games.middleware.auth_session_observability.RequestCorrelationMiddleware',
            'games.middleware.db_transient_response.TransientDatabaseResponseMiddleware',
            'games.tests.test_db_transient_response._AsgiPreViewFailureMiddleware',
        ],
        ALLOWED_HOSTS=['testserver'], DEBUG=False, DEBUG_PROPAGATE_EXCEPTIONS=False,
    )
    def test_concurrent_asgi_requests_keep_transient_state_isolated(self):
        async def run():
            app = ASGIHandler()
            return await asyncio.gather(
                self._asgi_call(app, '/asgi-fail/'),
                self._asgi_call(app, '/asgi-ok/'),
            )
        failed, healthy = asyncio.run(run())
        self.assertEqual(failed[0], 503)
        self.assertEqual(healthy, (200, b'healthy'))

    @override_settings(
        ROOT_URLCONF=__name__,
        MIDDLEWARE=[
            'games.middleware.auth_session_observability.RequestCorrelationMiddleware',
            'games.middleware.db_transient_response.TransientDatabaseResponseMiddleware',
        ],
        ALLOWED_HOSTS=['testserver'], DEBUG=False, DEBUG_PROPAGATE_EXCEPTIONS=False,
    )
    def test_asgi_cancellation_propagates_without_synthetic_response(self):
        async def run():
            global _asgi_started
            _asgi_started = asyncio.Event()
            app = ASGIHandler()
            task = asyncio.create_task(self._asgi_call(app, '/asgi-cancel/'))
            await _asgi_started.wait()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                return True
            return False
        self.assertTrue(asyncio.run(run()))

    @override_settings(
        ROOT_URLCONF=__name__,
        SESSION_ENGINE='django.contrib.sessions.backends.db',
        MIDDLEWARE=[
            'games.middleware.auth_session_observability.RequestCorrelationMiddleware',
            'games.middleware.db_transient_response.TransientDatabaseResponseMiddleware',
            'django.contrib.sessions.middleware.SessionMiddleware',
            'games.tests.test_db_transient_response.SessionLoadFailureMiddleware',
        ],
    )
    def test_actual_django_session_middleware_failure_becomes_503(self):
        client = Client(raise_request_exception=False)
        client.cookies['sessionid'] = 'valid-looking-session-key'
        with patch.object(SessionStore, 'load', side_effect=OperationalError(1040, 'Too many connections')):
            response = client.get('/session-probe/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Retry-After'], '2')
        self.assertRegex(response['X-Request-ID'], r'^[0-9a-f]{32}$')

    @override_settings(
        ROOT_URLCONF=__name__,
        MIDDLEWARE=[
            'games.middleware.auth_session_observability.RequestCorrelationMiddleware',
            'games.middleware.db_transient_response.TransientDatabaseResponseMiddleware',
        ],
    )
    def test_actual_django_timing_view_connection_failure_becomes_503(self):
        response = Client(raise_request_exception=False).post('/ladder/10/timing/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Retry-After'], '2')

    @override_settings(
        ROOT_URLCONF=__name__,
        MIDDLEWARE=[
            'games.middleware.auth_session_observability.RequestCorrelationMiddleware',
            'games.middleware.db_transient_response.TransientDatabaseResponseMiddleware',
        ],
    )
    def test_actual_timing_unknown_commit_is_503_but_other_write_remains_500(self):
        client = Client(raise_request_exception=False)
        timing = client.post('/ladder/11/timing/')
        legacy_timing = client.post('/ladder/12/timing/')
        other = client.post('/mutate/')
        self.assertEqual(timing.status_code, 503)
        self.assertEqual(legacy_timing.status_code, 500)
        self.assertEqual(other.status_code, 500)
