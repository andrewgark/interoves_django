"""Low overhead event-loop delay logging for each ASGI process."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import faulthandler
import logging
import os
import socket
import sys
import threading
import time
import weakref


logger = logging.getLogger("application")


class EventLoopStallWatchdog:
    """Report one all-thread Python traceback when ASGI stops heartbeating."""

    def __init__(self, *, timeout_seconds: float = 5.0):
        if timeout_seconds <= 0:
            raise ValueError("watchdog timeout must be positive")
        self.timeout_seconds = timeout_seconds
        self._pid = os.getpid()
        self._instance = socket.gethostname() or "unknown"
        self._condition = threading.Condition()
        self._stopped = False
        self._last_heartbeat = time.monotonic()
        self._reported_stall = False
        self._started = False
        self._thread = threading.Thread(
            target=self._run,
            name="asgi-stall-watchdog",
            daemon=True,
        )

    def start(self) -> None:
        with self._condition:
            if self._started:
                return
            self._started = True
            self._last_heartbeat = time.monotonic()
            self._thread.start()

    def beat(self) -> None:
        with self._condition:
            self._last_heartbeat = time.monotonic()
            self._reported_stall = False
            self._condition.notify()

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._stopped:
                    return
                elapsed = time.monotonic() - self._last_heartbeat
                if elapsed < self.timeout_seconds:
                    self._condition.wait(self.timeout_seconds - elapsed)
                    continue
                if self._reported_stall:
                    self._condition.wait()
                    continue
                self._reported_stall = True
                stalled_seconds = elapsed

            logger.error(
                "event=asgi_event_loop_stall instance=%s pid=%s timestamp=%s "
                "stalled_seconds=%.3f threads=%s",
                self._instance,
                self._pid,
                datetime.now(timezone.utc).isoformat(),
                stalled_seconds,
                threading.active_count(),
            )
            try:
                # faulthandler writes frame locations only, not local values.
                # Daphne redirects stderr to its managed process log stream.
                faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
            except (OSError, RuntimeError):
                logger.exception(
                    "event=asgi_event_loop_stall_traceback_failed instance=%s pid=%s",
                    self._instance, self._pid,
                )


class EventLoopLagMonitorMiddleware:
    """Start one periodic lag monitor whenever an ASGI loop serves a scope."""

    def __init__(self, app, *, interval_seconds: float = 10.0):
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.app = app
        self.interval_seconds = interval_seconds
        self._tasks = weakref.WeakKeyDictionary()
        self._pid = os.getpid()
        self._instance = socket.gethostname() or "unknown"
        self._stall_watchdog = EventLoopStallWatchdog()

    def _ensure_monitor(self, loop):
        task = self._tasks.get(loop)
        if task is None or task.done():
            self._stall_watchdog.start()
            loop.create_task(self._heartbeat())
            task = loop.create_task(self._monitor(loop))
            self._tasks[loop] = task

    async def _heartbeat(self):
        while True:
            self._stall_watchdog.beat()
            await asyncio.sleep(0.5)

    async def _monitor(self, loop):
        interval = self.interval_seconds
        while True:
            expected = loop.time() + interval
            await asyncio.sleep(interval)
            lag = max(0.0, loop.time() - expected)
            logger.info(
                "asgi_event_loop_lag seconds=%.3f pid=%s instance=%s threads=%s",
                lag,
                self._pid,
                self._instance,
                threading.active_count(),
            )

    async def __call__(self, scope, receive, send):
        self._ensure_monitor(asyncio.get_running_loop())
        await self.app(scope, receive, send)
