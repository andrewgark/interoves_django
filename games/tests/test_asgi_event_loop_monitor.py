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
