"""Opt-in cross-process integration tests against a disposable real Redis."""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest import SkipTest, skipUnless

from django.conf import settings
from django.core.cache import caches
from django.test import SimpleTestCase


REAL_REDIS_ENABLED = os.environ.get('INTEROVES_REAL_REDIS_TESTS', '').lower() in (
    '1', 'true', 'yes',
)
LOCAL_REDIS_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})


@skipUnless(
    REAL_REDIS_ENABLED,
    'set INTEROVES_REAL_REDIS_TESTS=1 and point REDIS_HOST at disposable Redis',
)
class RealtimeRedisProcessTests(SimpleTestCase):
    """Redis must share channel delivery and actor revisions across processes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        host = os.environ.get('REDIS_HOST', '')
        allow_remote = os.environ.get('INTEROVES_ALLOW_REMOTE_REDIS_TESTS') == '1'
        if not host:
            raise AssertionError('REDIS_HOST is required for real-Redis tests')
        if host not in LOCAL_REDIS_HOSTS and not allow_remote:
            raise SkipTest(
                'Refusing non-loopback Redis; set INTEROVES_ALLOW_REMOTE_REDIS_TESTS=1 '
                'only for a disposable CI service',
            )
        cls.repo_root = Path(settings.BASE_DIR)
        cls.worker = 'games.tests.realtime_redis_worker'

    def worker_command(self, *args):
        return [sys.executable, '-m', self.worker, *map(str, args)]

    def worker_environment(self):
        environment = os.environ.copy()
        environment.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
        return environment

    def parse_worker_output(self, output):
        lines = [line for line in output.splitlines() if line.strip()]
        self.assertTrue(lines, 'Redis worker returned no JSON output')
        return json.loads(lines[-1])

    def test_group_message_crosses_process_boundary(self):
        self.assertEqual(
            settings.CHANNEL_LAYERS['default']['BACKEND'],
            'channels_redis.core.RedisChannelLayer',
        )
        group = 'track.redis_test.{}'.format(uuid.uuid4().hex)
        namespace = 'game:redis-test:team:{}'.format(uuid.uuid4().hex)
        message = {
            'type': 'task.changed',
            'task': 123,
            'by': 'team',
            'seq': 7,
            'seq_namespace': namespace,
            'update_task_html_new': {'123': '<div data-solved="1"></div>'},
        }

        with tempfile.TemporaryDirectory(prefix='interoves-redis-test-') as tmp:
            ready_file = Path(tmp) / 'receiver.ready'
            receiver = subprocess.Popen(
                self.worker_command('receive', group, ready_file),
                cwd=self.repo_root,
                env=self.worker_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 10
                while not ready_file.exists() and time.monotonic() < deadline:
                    if receiver.poll() is not None:
                        break
                    time.sleep(0.05)
                if not ready_file.exists():
                    stdout, stderr = receiver.communicate(timeout=2)
                    self.fail(
                        'Redis receiver did not become ready: stdout={!r} stderr={!r}'.format(
                            stdout, stderr,
                        )
                    )

                publisher = subprocess.run(
                    self.worker_command('publish', group, json.dumps(message)),
                    cwd=self.repo_root,
                    env=self.worker_environment(),
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(publisher.returncode, 0, publisher.stderr)
                stdout, stderr = receiver.communicate(timeout=12)
                self.assertEqual(receiver.returncode, 0, stderr)
            finally:
                if receiver.poll() is None:
                    receiver.terminate()
                    try:
                        receiver.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        receiver.kill()
                        receiver.wait(timeout=2)

        self.assertEqual(self.parse_worker_output(stdout), message)

    def test_revision_allocation_is_atomic_across_processes(self):
        backend = settings.CACHES['track_revisions']['BACKEND']
        self.assertEqual(backend, 'django.core.cache.backends.redis.RedisCache')
        namespace = 'redis-test:{}'.format(uuid.uuid4().hex)
        key = 'track:seq:{}'.format(namespace)
        cache = caches['track_revisions']
        cache.delete(key)
        processes = [
            subprocess.Popen(
                self.worker_command('sequence', namespace, 25),
                cwd=self.repo_root,
                env=self.worker_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        try:
            outputs = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, stderr)
                outputs.extend(self.parse_worker_output(stdout))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=2)
            cache.delete(key)

        self.assertEqual(sorted(outputs), list(range(1, 51)))
