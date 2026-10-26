import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import games.realtime.admission as admission
from games.realtime.admission import DatabaseAdmissionASGIMiddleware
from interoves_django.asgi import _http_application_with_admission


class AsgiAdmissionConfigTests(TestCase):
    def test_web_runtime_gets_bounded_default_and_health_path_is_exempt(self):
        with patch.dict(os.environ, {'INTEROVES_RUNTIME_ROLE': 'web'}, clear=True):
            middleware = _http_application_with_admission(lambda *_args: None)
        self.assertIsInstance(middleware, DatabaseAdmissionASGIMiddleware)
        self.assertEqual(middleware.gate.stats()['configured_cap'], 4)
        self.assertEqual(middleware.gate.stats()['configured_max_waiters'], 8)
        self.assertEqual(middleware.gate.stats()['configured_wait_timeout_ms'], 1000)

    def test_worker_runtime_remains_unlimited_by_http_gate(self):
        with patch.dict(os.environ, {'INTEROVES_RUNTIME_ROLE': 'background-worker'}, clear=True):
            middleware = _http_application_with_admission(lambda *_args: None)
        self.assertEqual(middleware.gate.stats()['configured_cap'], 0)
        self.assertEqual(middleware.gate.stats()['configured_max_waiters'], 0)

    def test_environment_can_override_web_default(self):
        with patch.dict(os.environ, {
            'INTEROVES_RUNTIME_ROLE': 'web',
            'DB_ADMISSION_MAX_ACTIVE': '2',
            'DB_ADMISSION_MAX_WAITERS': '3',
            'DB_ADMISSION_WAIT_TIMEOUT_SECONDS': '0.25',
        }, clear=True):
            middleware = _http_application_with_admission(lambda *_args: None)
        stats = middleware.gate.stats()
        self.assertEqual(stats['configured_cap'], 2)
        self.assertEqual(stats['configured_max_waiters'], 3)
        self.assertEqual(stats['configured_wait_timeout_ms'], 250)

    def test_deployment_identity_reads_the_checkout_root(self):
        root = DatabaseAdmissionASGIMiddleware._repository_root()
        self.assertEqual(root, Path(admission.__file__).resolve().parents[2])
        self.assertTrue((root / 'manage.py').is_file())
        self.assertEqual(
            root / 'games' / 'realtime' / 'admission.py',
            Path(admission.__file__).resolve(),
        )
