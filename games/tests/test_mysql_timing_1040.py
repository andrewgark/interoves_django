"""Opt-in real-MySQL 1040 coverage; never runs against an arbitrary database."""

import asyncio
import json
import os
import socket
from unittest import skipUnless

import MySQLdb
from django.core.handlers.asgi import ASGIHandler
from django.test import SimpleTestCase, override_settings
from django.urls import path

from games.site.views.daily.timing import daily_solve_timing


urlpatterns = [
    path(
        'ladder/1/timing/', daily_solve_timing,
        {'game_id': 'ladder', 'number': '1'},
    ),
]


def _loopback_host(host):
    if host in ('localhost', '127.0.0.1', '::1'):
        return True
    try:
        return all(
            item[4][0].startswith(('127.', '::1'))
            for item in socket.getaddrinfo(host, None)
        )
    except OSError:
        return False


@skipUnless(
    os.environ.get('INTEROVES_RUN_MYSQL_1040_TEST') == '1',
    'requires explicit opt-in and a disposable low-limit local MySQL',
)
class TimingMySQL1040AsgiTests(SimpleTestCase):
    databases = {'default'}

    def setUp(self):
        from django.db import connection

        if connection.vendor != 'mysql' or not _loopback_host(connection.settings_dict.get('HOST', '')):
            self.skipTest('requires MySQL on loopback')
        if not str(connection.settings_dict.get('NAME', '')).startswith('test_'):
            self.skipTest('refusing to run outside a Django test database')
        with connection.cursor() as cursor:
            cursor.execute('SELECT @@GLOBAL.max_connections')
            max_connections = int(cursor.fetchone()[0])
        if max_connections > 20:
            self.skipTest('refusing to occupy more than 20 local MySQL connection slots')

    @staticmethod
    async def _asgi_request(
        path, *, method='GET', session_key=None, payload=None,
        csrf_token=None, csrf_cookie=None,
    ):
        sent = []
        body = json.dumps(payload or {}).encode() if method == 'POST' else b''

        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}

        async def send(message):
            sent.append(message)

        headers = [(b'host', b'testserver')]
        if method == 'POST':
            headers.append((b'content-type', b'application/json'))
        cookies = []
        if session_key:
            from django.conf import settings
            cookies.append('{}={}'.format(settings.SESSION_COOKIE_NAME, session_key))
        if csrf_cookie:
            cookies.append('csrftoken=' + csrf_cookie)
        if cookies:
            headers.append((b'cookie', '; '.join(cookies).encode()))
        if csrf_token:
            headers.append((b'x-csrftoken', csrf_token.encode()))
        scope = {
            'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
            'method': method, 'scheme': 'http', 'path': path,
            'raw_path': path.encode(), 'query_string': b'',
            'headers': headers, 'client': ('127.0.0.1', 1234),
            'server': ('testserver', 80),
        }
        await ASGIHandler()(scope, receive, send)
        start = next(message for message in sent if message['type'] == 'http.response.start')
        body = b''.join(
            message.get('body', b'') for message in sent
            if message['type'] == 'http.response.body'
        )
        headers = {key.lower(): value for key, value in start.get('headers', [])}
        return start['status'], headers, body

    @override_settings(ROOT_URLCONF=__name__)
    def test_real_1040_on_timing_get_is_503_and_recovers(self):
        from django.db import connection

        cfg = connection.settings_dict
        from django.contrib.sessions.backends.db import SessionStore

        session = SessionStore()
        session['phase20_probe'] = 'valid-local-session'
        session.save()
        from django.middleware.csrf import get_token
        from django.test import RequestFactory

        csrf_request = RequestFactory().get('/')
        csrf_token = get_token(csrf_request)
        csrf_cookie = csrf_request.META['CSRF_COOKIE']
        kwargs = {
            'host': cfg['HOST'], 'port': int(cfg.get('PORT') or 3306),
            'user': cfg['USER'], 'passwd': cfg['PASSWORD'],
            'db': cfg['NAME'], 'charset': 'utf8mb4',
        }
        if cfg.get('OPTIONS', {}).get('unix_socket'):
            kwargs.pop('host', None)
            kwargs.pop('port', None)
            kwargs['unix_socket'] = cfg['OPTIONS']['unix_socket']

        held = []
        try:
            while True:
                try:
                    held.append(MySQLdb.connect(**kwargs))
                except MySQLdb.OperationalError as exc:
                    if exc.args and exc.args[0] == 1040:
                        break
                    raise
            self.assertTrue(held, 'the opt-in test did not exhaust any application slots')
            path = '/ladder/1/timing/'
            session_status, session_headers, _ = asyncio.run(
                self._asgi_request(path, session_key=session.session_key)
            )
            self.assertEqual(session_status, 503)
            self.assertEqual(session_headers[b'retry-after'], b'2')

            status, headers, body = asyncio.run(self._asgi_request(path))
            self.assertEqual(status, 503, body.decode(errors='replace'))
            self.assertEqual(headers[b'retry-after'], b'2')
            self.assertEqual(headers[b'cache-control'], b'no-store')
            self.assertIn(b'temporarily_unavailable', body)

            # Connection exhaustion at the timing target lookup happens before
            # any event can be applied. Keep one stable envelope to prove this
            # POST receives the same retryable response as a safe GET.
            post_status, post_headers, post_body = asyncio.run(self._asgi_request(
                path,
                method='POST',
                payload={
                    'action': 'start',
                    'session_id': '00000000-0000-4000-8000-000000001040',
                    'event_id': 'phase22-1040-event',
                    'event_protocol': 1,
                    'event_capability': 'stable-event-v1',
                    'seq': 1,
                },
                csrf_token=csrf_token,
                csrf_cookie=csrf_cookie,
            ))
            self.assertEqual(post_status, 503, post_body.decode(errors='replace'))
            self.assertEqual(post_headers[b'retry-after'], b'2')
            self.assertEqual(post_headers[b'cache-control'], b'no-store')
            self.assertIn(b'temporarily_unavailable', post_body)
        finally:
            for raw_connection in held:
                raw_connection.close()

        # Once capacity returns, the same GET reaches normal view semantics.
        status, _headers, _body = asyncio.run(self._asgi_request(path))
        self.assertNotEqual(status, 503)
        session_status, _headers, _body = asyncio.run(
            self._asgi_request(path, session_key=session.session_key)
        )
        self.assertNotEqual(session_status, 503)
