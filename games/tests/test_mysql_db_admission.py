"""Opt-in real-MySQL checks for ASGI admission and DB-context concurrency."""

import asyncio
import json
import math
import os
import socket
import statistics
import threading
import time
from unittest import skipUnless

from django.core.handlers.asgi import ASGIHandler
from django.db import connection
from django.http import JsonResponse
from django.test import SimpleTestCase, override_settings
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from games.asgi_admission import DatabaseAdmissionASGIMiddleware


_active_db_sections = 0
_peak_db_sections = 0
_connection_ids = set()
_counter_lock = threading.Lock()


@csrf_exempt
def db_probe(request):
    global _active_db_sections, _peak_db_sections
    with _counter_lock:
        _active_db_sections += 1
        _peak_db_sections = max(_peak_db_sections, _active_db_sections)
    try:
        # Force the DB-backed session to be read inside the admitted request.
        session_marker = request.session.get('admission_probe')
        # The deliberate server-side pause holds a real independent connection
        # while concurrent requests enter Django's request-specific executor.
        with connection.cursor() as cursor:
            cursor.execute('SELECT CONNECTION_ID(), SLEEP(0.04)')
            connection_id, _ = cursor.fetchone()
        with _counter_lock:
            _connection_ids.add(int(connection_id))
        return JsonResponse({'ok': True, 'session_marker': session_marker})
    finally:
        with _counter_lock:
            _active_db_sections -= 1


urlpatterns = [path('test/db-admission/', db_probe)]


def _is_loopback(host):
    if host in ('localhost', '127.0.0.1', '::1'):
        return True
    try:
        return all(item[4][0].startswith(('127.', '::1')) for item in socket.getaddrinfo(host, None))
    except OSError:
        return False


async def _request(app, method, session_key, cookie_name):
    messages = []
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def send(message):
        messages.append(message)
    path = '/test/db-admission/'
    await app({
        'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
        'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
        'query_string': b'', 'headers': [
            (b'host', b'testserver'),
            (b'cookie', (cookie_name + '=' + session_key).encode()),
        ],
        'client': ('127.0.0.1', 1234), 'server': ('testserver', 80),
    }, receive, send)
    start = next(message for message in messages if message['type'] == 'http.response.start')
    body = b''.join(
        message.get('body', b'') for message in messages
        if message['type'] == 'http.response.body'
    )
    headers = {key.lower(): value for key, value in start.get('headers', [])}
    return start['status'], headers, body


def _percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * p) - 1))]


@skipUnless(os.environ.get('INTEROVES_RUN_MYSQL_ADMISSION_TEST') == '1',
            'requires explicit opt-in and disposable loopback MySQL')
class MySQLASGIAdmissionTests(SimpleTestCase):
    databases = {'default'}

    def setUp(self):
        if connection.vendor != 'mysql' or not _is_loopback(connection.settings_dict.get('HOST', '')):
            self.skipTest('requires MySQL on loopback')
        if not str(connection.settings_dict.get('NAME', '')).startswith('test_'):
            self.skipTest('refusing to use a non-test database')
        with connection.cursor() as cursor:
            cursor.execute('SELECT @@GLOBAL.max_connections')
            if int(cursor.fetchone()[0]) > 100:
                self.skipTest('test requires a deliberately bounded local MySQL')

    @override_settings(ROOT_URLCONF=__name__)
    def test_real_django_asgi_db_concurrency_at_caps_0_1_2_4(self):
        global _active_db_sections, _peak_db_sections, _connection_ids
        from django.conf import settings
        from django.contrib.sessions.backends.db import SessionStore

        session = SessionStore()
        session['admission_probe'] = 'mysql'
        session.save()
        session_cookie_name = settings.SESSION_COOKIE_NAME
        observations = []
        for cap in (0, 1, 2, 4):
            with _counter_lock:
                _active_db_sections = 0
                _peak_db_sections = 0
                _connection_ids = set()
            # A fresh handler ensures the overridden URLconf is loaded, and
            # the outer wrapper sits before DB-backed session middleware.
            app = DatabaseAdmissionASGIMiddleware(
                ASGIHandler(), max_active=cap,
                max_waiters=8 if cap else 0,
                wait_timeout=2 if cap else 0,
            )
            async def run_batch():
                started = time.perf_counter()
                latencies = []
                async def one(method):
                    t0 = time.perf_counter()
                    status, _headers, body = await _request(
                        app, method, session.session_key, session_cookie_name,
                    )
                    if status == 200:
                        assert json.loads(body)['session_marker'] == 'mysql'
                    latencies.append((time.perf_counter() - t0) * 1000)
                    return status
                statuses = await asyncio.gather(*(
                    one('GET' if index % 2 == 0 else 'POST') for index in range(8)
                ))
                return statuses, latencies, (time.perf_counter() - started) * 1000

            statuses, latencies, elapsed = asyncio.run(run_batch())
            with _counter_lock:
                peak = _peak_db_sections
                connection_ids = len(_connection_ids)
            self.assertEqual(statuses, [200] * 8)
            if cap:
                self.assertLessEqual(peak, cap)
            else:
                # Observe-only must admit concurrent DB-backed requests rather
                # than accidentally serializing them through a hidden queue.
                self.assertGreater(peak, 1)
            self.assertGreater(connection_ids, 0)
            self.assertEqual(app.gate.stats()['active'], 0)
            self.assertEqual(app.gate.stats()['waiting'], 0)
            if cap:
                self.assertLessEqual(app.gate.stats()['max_active_observed'], cap)
            else:
                self.assertGreater(app.gate.stats()['max_active_observed'], 1)
            self.assertEqual(app.gate.stats()['active_permits'], 0)
            observations.append({
                'cap': cap,
                'peak_db_queries': peak,
                'distinct_mysql_connections': connection_ids,
                'throughput_per_second': round(8 / (elapsed / 1000), 2),
                'p95_ms': round(_percentile(latencies, .95), 2),
                'p99_ms': round(_percentile(latencies, .99), 2),
            })

        saturated_app = DatabaseAdmissionASGIMiddleware(
            ASGIHandler(), max_active=1, max_waiters=0, wait_timeout=0,
        )
        statuses, headers, _body = asyncio.run(
            _run_saturated(saturated_app, session, session_cookie_name),
        )
        self.assertIn(200, statuses)
        self.assertIn(503, statuses)
        rejection_headers = headers[statuses.index(503)]
        self.assertEqual(rejection_headers.get(b'retry-after'), b'2')
        self.assertEqual(rejection_headers.get(b'cache-control'), b'no-store')
        self.assertIn(b'x-request-id', rejection_headers)
        self.assertEqual(saturated_app.gate.stats()['active'], 0)
        print('MYSQL_ADMISSION_QUEUE_EXHAUSTION=' + json.dumps({
            'status_200': statuses.count(200),
            'status_503': statuses.count(503),
            'rejected': saturated_app.gate.stats()['rejected'],
        }, sort_keys=True))
        print('MYSQL_ADMISSION_OBSERVATIONS=' + json.dumps(observations, sort_keys=True))


async def _run_saturated(app, session, cookie_name):
    statuses = await asyncio.gather(*(
        _request(app, 'GET' if index % 2 == 0 else 'POST', session.session_key, cookie_name)
        for index in range(8)
    ))
    return (
        [item[0] for item in statuses],
        [item[1] for item in statuses],
        [item[2] for item in statuses],
    )
