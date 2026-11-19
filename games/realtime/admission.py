"""Bound concurrent HTTP requests before Django creates request sync contexts.

This is an instance-local request admission gate. It deliberately does not
claim to cap database connections opened by WebSockets or unrelated threads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import time
import uuid
from collections import deque
from pathlib import Path

logger = logging.getLogger('games.db_admission')
_DURATION_BUCKETS_MS = (5, 20, 50, 100, 250, 500, 1000, 5000)
_METRICS_INTERVAL_SECONDS = 10.0


def _new_duration_histogram():
    histogram = {str(bound): 0 for bound in _DURATION_BUCKETS_MS}
    histogram['inf'] = 0
    return histogram


def _observe_duration(histogram, value_ms):
    for bound in _DURATION_BUCKETS_MS:
        if value_ms <= bound:
            histogram[str(bound)] += 1
    histogram['inf'] += 1


# Players' moves: answers, hints and solve-timing writes. They are short and
# a player is waiting on them; page reads (results tables, statistics) are
# the long requests that filled every permit at the 00:00 MSK release.
_PRIORITY_POST_RE = re.compile(
    r'^/(?:send_attempt|send_raddle_ui)/'
    r'|/(?:guess|timing|hint)/$'
)


def is_priority_request(scope) -> bool:
    return scope.get('method') == 'POST' and bool(_PRIORITY_POST_RE.search(scope.get('path') or ''))


class _AdmissionGate:
    def __init__(
        self, *, max_active: int, max_waiters: int, wait_timeout: float,
        priority_reserved: int = 0,
    ):
        if max_active < 0 or max_waiters < 0 or wait_timeout < 0 or priority_reserved < 0:
            raise ValueError('Admission limits cannot be negative.')
        if priority_reserved and priority_reserved >= max_active:
            raise ValueError('Reserved priority permits must leave one for other requests.')
        if max_active == 0 and (max_waiters or wait_timeout):
            raise ValueError('Queue settings require an enabled active request cap.')
        if max_waiters and wait_timeout <= 0:
            raise ValueError('A finite positive wait timeout is required for queued requests.')
        self.max_active = max_active
        self.max_waiters = max_waiters
        self.wait_timeout = wait_timeout
        # Ordinary requests may hold at most this many permits; the rest are
        # kept for priority requests (players' moves).
        self.ordinary_limit = max_active - priority_reserved
        self.active = 0
        self.ordinary_active = 0
        self.max_active_observed = 0
        self.max_active_since_sample = 0
        self.waiters: deque[asyncio.Future] = deque()
        self.priority_waiters: deque[asyncio.Future] = deque()
        self.lock = asyncio.Lock()
        self.accepted = 0
        self.rejected = 0
        self.queue_wait_ms_total = 0.0
        self.request_time_ms_total = 0.0
        self.admission_count = 0
        self.admission_wait_ms_max = 0.0
        self.permit_hold_ms_count = 0
        self.permit_hold_ms_max = 0.0
        self.cancellations = 0
        self.rejections_by_reason = {'queue_full': 0, 'queue_timeout': 0}
        self.admission_wait_ms_buckets = _new_duration_histogram()
        self.permit_hold_ms_buckets = _new_duration_histogram()

    def _can_grant_locked(self, priority: bool) -> bool:
        if self.active >= self.max_active:
            return False
        return priority or self.ordinary_active < self.ordinary_limit

    def _grant_locked(self, priority: bool) -> None:
        self.active += 1
        if not priority:
            self.ordinary_active += 1
        self.max_active_observed = max(self.max_active_observed, self.active)
        self.max_active_since_sample = max(self.max_active_since_sample, self.active)

    @staticmethod
    def _pending(queue) -> bool:
        return any(not item.done() for item in queue)

    async def acquire(self, priority: bool = False) -> tuple[bool, float, str | None]:
        queued_at = time.monotonic()
        loop = asyncio.get_running_loop()
        queue = self.priority_waiters if priority else self.waiters
        async with self.lock:
            ahead = self._pending(self.priority_waiters) or (
                not priority and self._pending(self.waiters)
            )
            if self.max_active == 0 or (self._can_grant_locked(priority) and not ahead):
                self._grant_locked(priority)
                self.accepted += 1
                self.admission_count += 1
                self._record_wait(0)
                return True, 0.0, None
            if len(queue) >= self.max_waiters:
                self.rejected += 1
                self.rejections_by_reason['queue_full'] += 1
                self._record_wait(0)
                return False, 0.0, 'queue_full'
            future = loop.create_future()
            queue.append(future)

        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=self.wait_timeout)
            waited_ms = (time.monotonic() - queued_at) * 1000
            async with self.lock:
                self.accepted += 1
                self.admission_count += 1
                self._record_wait(waited_ms)
            return True, waited_ms, None
        except asyncio.CancelledError:
            cleanup = asyncio.create_task(self._remove_cancelled_waiter(future, queued_at, priority))
            await asyncio.shield(cleanup)
            raise
        except asyncio.TimeoutError:
            waited_ms = (time.monotonic() - queued_at) * 1000
            async with self.lock:
                try:
                    queue.remove(future)
                except ValueError:
                    # A just-granted permit must not be lost on timeout.
                    if future.done() and not future.cancelled() and future.result():
                        self._release_locked(priority)
                self.rejected += 1
                self.rejections_by_reason['queue_timeout'] += 1
                self._record_wait(waited_ms)
            return False, waited_ms, 'queue_timeout'

    def _record_wait(self, waited_ms: float) -> None:
        self.queue_wait_ms_total += waited_ms
        self.admission_wait_ms_max = max(self.admission_wait_ms_max, waited_ms)
        _observe_duration(self.admission_wait_ms_buckets, waited_ms)

    async def _remove_cancelled_waiter(
        self, future: asyncio.Future, queued_at: float, priority: bool = False,
    ) -> None:
        queue = self.priority_waiters if priority else self.waiters
        async with self.lock:
            self.cancellations += 1
            self._record_wait((time.monotonic() - queued_at) * 1000)
            try:
                queue.remove(future)
            except ValueError:
                # The permit was granted just as this waiter was cancelled.
                # Return it to the next waiter safely.
                if future.done() and not future.cancelled() and future.result():
                    self._release_locked(priority)

    def _release_locked(self, priority: bool = False) -> None:
        self.active -= 1
        if not priority:
            self.ordinary_active -= 1
        self._dispatch_locked()

    def _dispatch_locked(self) -> None:
        """Hand free permits to waiters: players' moves first, then the rest."""
        for queue, priority in ((self.priority_waiters, True), (self.waiters, False)):
            while queue and self._can_grant_locked(priority):
                waiter = queue.popleft()
                if waiter.done():
                    continue
                self._grant_locked(priority)
                waiter.set_result(True)

    async def release(self, held_ms: float, priority: bool = False) -> None:
        async with self.lock:
            self.request_time_ms_total += held_ms
            self.permit_hold_ms_count += 1
            self.permit_hold_ms_max = max(self.permit_hold_ms_max, held_ms)
            _observe_duration(self.permit_hold_ms_buckets, held_ms)
            self._release_locked(priority)

    def stats(self) -> dict[str, int | float]:
        return {
            'active': self.active,
            'active_requests': self.active,
            'active_permits': self.active if self.max_active else 0,
            'max_active_observed': self.max_active_observed,
            'max_active_since_sample': self.max_active_since_sample,
            'waiting': sum(
                not item.done() for item in (*self.waiters, *self.priority_waiters)
            ),
            'waiting_priority': sum(not item.done() for item in self.priority_waiters),
            'configured_priority_reserved': self.max_active - self.ordinary_limit,
            'accepted': self.accepted,
            'rejected': self.rejected,
            'configured_cap': self.max_active,
            'configured_max_waiters': self.max_waiters,
            'configured_wait_timeout_ms': round(self.wait_timeout * 1000, 2),
            'admission_count': self.admission_count,
            'queue_wait_ms_total': round(self.queue_wait_ms_total, 2),
            'queue_wait_ms_max': round(self.admission_wait_ms_max, 2),
            'queue_wait_ms_buckets': dict(self.admission_wait_ms_buckets),
            'request_time_ms_total': round(self.request_time_ms_total, 2),
            'permit_hold_ms_count': self.permit_hold_ms_count,
            'permit_hold_ms_max': round(self.permit_hold_ms_max, 2),
            'permit_hold_ms_buckets': dict(self.permit_hold_ms_buckets),
            'cancellations': self.cancellations,
            'rejections_by_reason': dict(self.rejections_by_reason),
        }

    def take_metrics_snapshot(self) -> dict[str, int | float]:
        """Snapshot event-loop-owned counters and consume the interval high-water mark."""
        current = self.stats()
        self.max_active_since_sample = 0
        return current


class DatabaseAdmissionASGIMiddleware:
    """Limit HTTP concurrency before Django session/auth middleware runs."""

    def __init__(
        self,
        app,
        *,
        max_active: int,
        max_waiters: int,
        wait_timeout: float,
        retry_after_seconds: int = 2,
        metrics_interval_seconds: float = _METRICS_INTERVAL_SECONDS,
        priority_reserved: int = 0,
    ):
        if retry_after_seconds < 1 or retry_after_seconds > 30:
            raise ValueError('Retry-After must be between 1 and 30 seconds.')
        self.app = app
        self.gate = _AdmissionGate(
            max_active=max_active,
            max_waiters=max_waiters,
            wait_timeout=wait_timeout,
            priority_reserved=priority_reserved,
        )
        self.retry_after_seconds = retry_after_seconds
        if metrics_interval_seconds <= 0:
            raise ValueError('Metrics interval must be positive.')
        self.metrics_interval_seconds = metrics_interval_seconds
        self._metrics_last_emit_at = 0.0
        self._metrics_last_snapshot = self.gate.stats()
        self._metrics_task = None
        self._instance_label = os.environ.get('DB_ADMISSION_INSTANCE_ID') or socket.gethostname()
        deploy_version, source_commit = self._read_deployment_identity()
        self._deployment_version = os.environ.get('DB_ADMISSION_DEPLOYMENT_VERSION') or deploy_version
        self._source_commit = os.environ.get('DB_ADMISSION_SOURCE_COMMIT') or source_commit

    @staticmethod
    def _repository_root() -> Path:
        """Checkout root. This module lives in games/realtime, two directories down."""
        return Path(__file__).resolve().parents[2]

    @staticmethod
    def _read_deployment_identity() -> tuple[str, str]:
        """Read a bounded, local identity once at startup; never query metadata over the network."""
        root = DatabaseAdmissionASGIMiddleware._repository_root()
        try:
            version = (root / 'interoves_django/deploy_version.txt').read_text(encoding='utf-8').strip()[:64]
        except OSError:
            version = 'unknown'
        try:
            with (root / 'release-manifest.json').open(encoding='utf-8') as stream:
                source_commit = str(json.load(stream).get('source_commit', 'unknown'))[:64]
        except (OSError, ValueError):
            source_commit = 'unknown'
        return version or 'unknown', source_commit or 'unknown'

    def _ensure_metrics_sampler(self) -> None:
        task = self._metrics_task
        if task is None or task.done():
            self._metrics_task = asyncio.create_task(self._sample_metrics_while_active())

    async def _sample_metrics_while_active(self) -> None:
        """Sample active bursts periodically and flush their final counters after traffic stops."""
        try:
            while True:
                await asyncio.sleep(self.metrics_interval_seconds)
                self._maybe_emit_metrics(force=True)
                if self.gate.active == 0:
                    return
        finally:
            self._metrics_task = None

    def _maybe_emit_metrics(self, *, force: bool = False) -> None:
        """Write low-cardinality local log metrics; no exporter/network calls."""
        now = time.monotonic()
        if not force and now - self._metrics_last_emit_at < self.metrics_interval_seconds:
            return
        current = self.gate.take_metrics_snapshot()
        previous = self._metrics_last_snapshot

        def delta(key):
            return current[key] - previous[key]

        def bucket_delta(key):
            return {
                bucket: current[key][bucket] - previous[key][bucket]
                for bucket in current[key]
            }

        payload = {
            'event': 'db_admission_metrics',
            'service': 'interoves-web',
            'instance': self._instance_label,
            'deployment_version': self._deployment_version,
            'source_commit': self._source_commit,
            'configured_cap': current['configured_cap'],
            'configured_max_waiters': current['configured_max_waiters'],
            'configured_wait_timeout_ms': current['configured_wait_timeout_ms'],
            'active': current['active'],
            'active_requests': current['active'],
            'active_permits': current['active_permits'],
            'max_active_since_previous_sample': current['max_active_since_sample'],
            'max_active_observed': current['max_active_observed'],
            'sample_interval_seconds': self.metrics_interval_seconds,
            'queue_length': current['waiting'],
            'accepted_delta': delta('accepted'),
            'rejected_delta': delta('rejected'),
            'cancelled_delta': delta('cancellations'),
        }
        for reason in current['rejections_by_reason']:
            payload['rejected_{}_delta'.format(reason)] = (
                current['rejections_by_reason'][reason]
                - previous['rejections_by_reason'][reason]
            )
        for metric, key in (
            ('admission_wait', 'queue_wait_ms_buckets'),
            ('permit_hold', 'permit_hold_ms_buckets'),
        ):
            for bucket, count in bucket_delta(key).items():
                suffix = 'inf' if bucket == 'inf' else '{}ms'.format(bucket)
                payload['{}_le_{}_delta'.format(metric, suffix)] = count
        logger.info(json.dumps(payload, sort_keys=True, separators=(',', ':')))
        self._metrics_last_emit_at = now
        self._metrics_last_snapshot = current

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http':
            return await self.app(scope, receive, send)

        # The load balancer's liveness check must be able to distinguish a
        # responsive Django process from DB saturation. This view is explicitly
        # dependency-free, so let it run outside the DB admission queue.
        if scope.get('path') in ('/health/live', '/health/live/'):
            return await self.app(scope, receive, send)

        priority = is_priority_request(scope)
        try:
            admitted, waited_ms, rejection_reason = await self.gate.acquire(priority)
        except asyncio.CancelledError:
            self._maybe_emit_metrics()
            raise
        if not admitted:
            request_id = uuid.uuid4().hex
            # Rejections are aggregated into low-cardinality JSON samples. Do
            # not emit one warning per request during a sustained overload.
            self._maybe_emit_metrics()
            body = json.dumps({
                'ok': False,
                'error': 'temporarily_unavailable',
                'reason': rejection_reason,
            }, separators=(',', ':')).encode('ascii')
            await send({
                'type': 'http.response.start',
                'status': 503,
                'headers': [
                    (b'content-type', b'application/json'),
                    (b'content-length', str(len(body)).encode('ascii')),
                    (b'retry-after', str(self.retry_after_seconds).encode('ascii')),
                    (b'cache-control', b'no-store'),
                    (b'x-request-id', request_id.encode('ascii')),
                    (b'x-interoves-admission-rejection', rejection_reason.encode('ascii')),
                ],
            })
            await send({'type': 'http.response.body', 'body': body, 'more_body': False})
            return

        self._ensure_metrics_sampler()
        started = time.monotonic()
        try:
            await self.app(scope, receive, send)
        except asyncio.CancelledError:
            self.gate.cancellations += 1
            raise
        finally:
            held_ms = (time.monotonic() - started) * 1000
            release = asyncio.create_task(self.gate.release(held_ms, priority))
            await asyncio.shield(release)
            logger.debug(
                'event=released active=%d waiting=%d configured_cap=%d held_ms=%.1f queue_wait_ms=%.1f',
                self.gate.active, len(self.gate.waiters), self.gate.max_active, held_ms, waited_ms,
            )
            self._maybe_emit_metrics()
