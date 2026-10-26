import asyncio
import json

from django.test import SimpleTestCase

from games.realtime.admission import DatabaseAdmissionASGIMiddleware


async def _invoke(app, path='/admission-test/'):
    events = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        events.append(message)

    await app(
        {'type': 'http', 'asgi': {'version': '3.0'}, 'method': 'GET', 'path': path,
         'headers': [], 'query_string': b'', 'server': ('test', 80),
         'client': ('127.0.0.1', 12345), 'scheme': 'http', 'http_version': '1.1'},
        receive,
        send,
    )
    return events


class DatabaseAdmissionASGITests(SimpleTestCase):
    async def test_zero_cap_observes_inflight_http_without_limiting_it(self):
        entered = 0
        peak = 0
        started = asyncio.Queue()
        release = asyncio.Event()

        async def app(scope, receive, send):
            nonlocal entered, peak
            entered += 1
            peak = max(peak, entered)
            await started.put(True)
            await release.wait()
            entered -= 1
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        gate = DatabaseAdmissionASGIMiddleware(
            app, max_active=0, max_waiters=0, wait_timeout=0,
        )
        requests = [asyncio.create_task(_invoke(gate)) for _ in range(3)]
        for _ in requests:
            await started.get()
        self.assertEqual(peak, 3)
        self.assertEqual(gate.gate.stats()['active_requests'], 3)
        self.assertEqual(gate.gate.stats()['active_permits'], 0)
        release.set()
        await asyncio.gather(*requests)
        self.assertEqual(gate.gate.stats()['active'], 0)
        self.assertEqual(gate.gate.stats()['rejected'], 0)

    async def test_metrics_are_low_cardinality_json_deltas(self):
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        gate = DatabaseAdmissionASGIMiddleware(
            app, max_active=1, max_waiters=0, wait_timeout=0,
        )
        gate._metrics_last_emit_at = -60
        with self.assertLogs('games.db_admission', level='INFO') as captured:
            await _invoke(gate, '/user-specific/12345/')
        metric = json.loads(captured.output[0].split(':', 2)[-1].strip())
        self.assertEqual(metric['event'], 'db_admission_metrics')
        self.assertEqual(metric['accepted_delta'], 1)
        self.assertEqual(metric['active'], 0)
        self.assertNotIn('path', metric)
        self.assertTrue(metric['instance'])
        self.assertIn('deployment_version', metric)
        self.assertIn('source_commit', metric)
        self.assertEqual(metric['max_active_since_previous_sample'], 1)
        self.assertEqual(metric['permit_hold_le_inf_delta'], 1)

    async def test_short_inflight_peak_is_sampled_and_final_counters_flush_without_more_requests(self):
        started = asyncio.Queue()
        release = asyncio.Event()

        async def app(scope, receive, send):
            await started.put(True)
            await release.wait()
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        gate = DatabaseAdmissionASGIMiddleware(
            app, max_active=0, max_waiters=0, wait_timeout=0,
            metrics_interval_seconds=0.01,
        )
        with self.assertLogs('games.db_admission', level='INFO') as captured:
            requests = [asyncio.create_task(_invoke(gate)) for _ in range(3)]
            for _ in requests:
                await started.get()
            await asyncio.sleep(0.025)
            release.set()
            await asyncio.gather(*requests)
            if gate._metrics_task is not None:
                await gate._metrics_task

        samples = [
            json.loads(line.split(':', 2)[-1].strip())
            for line in captured.output
            if 'db_admission_metrics' in line
        ]
        self.assertTrue(any(sample['active'] == 3 for sample in samples), samples)
        self.assertTrue(any(sample['max_active_since_previous_sample'] == 3 for sample in samples), samples)
        self.assertTrue(any(sample['active'] == 0 for sample in samples), samples)
        self.assertEqual(sum(sample['accepted_delta'] for sample in samples), 3)
        self.assertEqual(gate.gate.stats()['max_active_observed'], 3)

    async def test_active_requests_are_bounded_and_queue_is_finite(self):
        entered = 0
        peak = 0
        release = asyncio.Event()
        started = asyncio.Queue()

        async def app(scope, receive, send):
            nonlocal entered, peak
            entered += 1
            peak = max(peak, entered)
            await started.put(True)
            await release.wait()
            entered -= 1
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        gate = DatabaseAdmissionASGIMiddleware(
            app, max_active=2, max_waiters=1, wait_timeout=1,
        )
        first = asyncio.create_task(_invoke(gate))
        second = asyncio.create_task(_invoke(gate))
        await started.get()
        await started.get()
        queued = asyncio.create_task(_invoke(gate))
        for _ in range(20):
            if gate.gate.stats()['waiting'] == 1:
                break
            await asyncio.sleep(0)
        rejected_events = await _invoke(gate)
        rejected_start = next(event for event in rejected_events if event['type'] == 'http.response.start')
        self.assertEqual(rejected_start['status'], 503)
        headers = dict(rejected_start['headers'])
        self.assertEqual(headers[b'retry-after'], b'2')
        self.assertEqual(headers[b'cache-control'], b'no-store')
        self.assertEqual(gate.gate.stats()['active'], 2)
        self.assertEqual(gate.gate.stats()['waiting'], 1)

        release.set()
        await asyncio.gather(first, second, queued)
        self.assertEqual(peak, 2)
        self.assertEqual(gate.gate.stats()['active'], 0)
        self.assertEqual(gate.gate.stats()['waiting'], 0)
        stats = gate.gate.stats()
        self.assertEqual(stats['configured_cap'], 2)
        self.assertEqual(stats['rejections_by_reason']['queue_full'], 1)
        self.assertEqual(stats['admission_count'], 3)
        self.assertEqual(stats['permit_hold_ms_count'], 3)
        self.assertGreaterEqual(stats['queue_wait_ms_buckets']['5'], 2)
        self.assertEqual(stats['permit_hold_ms_buckets']['inf'], 3)

    async def test_queue_timeout_returns_503_and_does_not_leak_permit(self):
        release = asyncio.Event()
        started = asyncio.Event()

        async def app(scope, receive, send):
            started.set()
            await release.wait()
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        gate = DatabaseAdmissionASGIMiddleware(
            app, max_active=1, max_waiters=1, wait_timeout=0.01,
        )
        first = asyncio.create_task(_invoke(gate))
        await started.wait()
        timed_out = await _invoke(gate)
        start = next(event for event in timed_out if event['type'] == 'http.response.start')
        self.assertEqual(start['status'], 503)
        self.assertEqual(gate.gate.stats()['active'], 1)
        self.assertEqual(gate.gate.stats()['waiting'], 0)
        release.set()
        await first
        self.assertEqual(gate.gate.stats()['active'], 0)
        self.assertEqual(gate.gate.stats()['rejections_by_reason']['queue_timeout'], 1)

    async def test_cancellation_while_waiting_and_while_admitted_releases_permit(self):
        release = asyncio.Event()
        started = asyncio.Event()

        async def app(scope, receive, send):
            started.set()
            await release.wait()
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        gate = DatabaseAdmissionASGIMiddleware(
            app, max_active=1, max_waiters=2, wait_timeout=1,
        )
        active = asyncio.create_task(_invoke(gate))
        await started.wait()
        waiting = asyncio.create_task(_invoke(gate))
        for _ in range(20):
            if gate.gate.stats()['waiting']:
                break
            await asyncio.sleep(0)
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        self.assertEqual(gate.gate.stats()['waiting'], 0)

        active.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await active
        self.assertEqual(gate.gate.stats()['active'], 0)
        self.assertEqual(gate.gate.stats()['cancellations'], 2)
        self.assertEqual(gate.gate.stats()['permit_hold_ms_count'], 1)

    async def test_application_exception_releases_permit(self):
        async def app(scope, receive, send):
            raise RuntimeError('expected test exception')

        gate = DatabaseAdmissionASGIMiddleware(
            app, max_active=1, max_waiters=0, wait_timeout=0,
        )
        with self.assertRaisesRegex(RuntimeError, 'expected test exception'):
            await _invoke(gate)
        self.assertEqual(gate.gate.stats()['active'], 0)

    async def test_non_http_scope_is_not_limited(self):
        called = []

        async def app(scope, receive, send):
            called.append(scope['type'])

        gate = DatabaseAdmissionASGIMiddleware(
            app, max_active=1, max_waiters=0, wait_timeout=0,
        )
        await gate({'type': 'lifespan'}, None, None)
        self.assertEqual(called, ['lifespan'])
        self.assertEqual(gate.gate.stats()['active'], 0)

    async def test_liveness_endpoint_bypasses_a_full_admission_queue(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def app(scope, receive, send):
            if scope['path'] == '/slow/':
                entered.set()
                await release.wait()
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        gate = DatabaseAdmissionASGIMiddleware(
            app, max_active=1, max_waiters=0, wait_timeout=0,
        )
        active = asyncio.create_task(_invoke(gate, '/slow/'))
        await entered.wait()
        health = await _invoke(gate, '/health/live/')
        health_start = next(event for event in health if event['type'] == 'http.response.start')
        self.assertEqual(health_start['status'], 200)
        self.assertEqual(gate.gate.stats()['active'], 1)
        self.assertEqual(gate.gate.stats()['rejected'], 0)
        release.set()
        await active
