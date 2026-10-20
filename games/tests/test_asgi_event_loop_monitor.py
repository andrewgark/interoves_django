import asyncio
import time
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from interoves_django.asgi_event_loop_monitor import (
    EventLoopLagMonitorMiddleware,
    EventLoopStallWatchdog,
)


class EventLoopLagMonitorMiddlewareTests(IsolatedAsyncioTestCase):
    async def test_monitor_starts_once_and_covers_http_and_websocket_scopes(self):
        async def app(scope, receive, send):
            return None

        middleware = EventLoopLagMonitorMiddleware(app, interval_seconds=60)
        monitor_started = asyncio.Event()
        allow_monitor_exit = asyncio.Event()

        async def monitor(loop):
            monitor_started.set()
            await allow_monitor_exit.wait()

        middleware._monitor = monitor
        loop = asyncio.get_running_loop()
        await middleware({"type": "http"}, None, None)
        first_task = middleware._tasks[loop]
        await middleware({"type": "websocket"}, None, None)

        self.assertIs(middleware._tasks[loop], first_task)
        await asyncio.wait_for(monitor_started.wait(), timeout=1)

        first_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first_task
        middleware._stall_watchdog.stop()

    async def test_memory_report_runs_every_n_ticks_and_survives_errors(self):
        middleware = EventLoopLagMonitorMiddleware(
            lambda *_args: None, interval_seconds=0.001, memory_report_every=2,
        )
        calls = []

        def report(**kwargs):
            calls.append(kwargs)
            raise RuntimeError('boom')

        middleware._memory_reporter.report = report
        loop = asyncio.get_running_loop()
        with self.assertLogs('application', level='INFO') as logs:
            task = loop.create_task(middleware._monitor(loop))
            while len(calls) < 2:
                await asyncio.sleep(0.005)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(calls[0], {'pid': middleware._pid, 'instance': middleware._instance})
        self.assertTrue(any('asgi_memory_report_failed' in line for line in logs.output))
        lag_lines = [line for line in logs.output if 'asgi_event_loop_lag' in line]
        self.assertGreaterEqual(len(lag_lines), 4)

    def test_rejects_non_positive_interval(self):
        with self.assertRaisesRegex(ValueError, "interval_seconds must be positive"):
            EventLoopLagMonitorMiddleware(lambda *_args: None, interval_seconds=0)


class EventLoopStallWatchdogTests(TestCase):
    def test_blocked_loop_emits_one_trace_per_stall_episode(self):
        watchdog = EventLoopStallWatchdog(timeout_seconds=0.04)
        with patch(
            'interoves_django.asgi_event_loop_monitor.faulthandler.dump_traceback',
        ) as dump:
            watchdog.start()
            watchdog.beat()
            # Deliberately block this test's event-loop thread. The independent
            # watchdog thread still detects the missing heartbeat.
            time.sleep(0.12)
            time.sleep(0.06)
            self.assertEqual(dump.call_count, 1)

            watchdog.beat()
            time.sleep(0.12)
            self.assertEqual(dump.call_count, 2)
        watchdog.stop()
