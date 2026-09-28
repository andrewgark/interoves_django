from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from games.worker_config import infer_worker_name, load_worker_config


class WorkerConfigTests(SimpleTestCase):
    def test_worker_is_inferred_from_existing_runtime_role(self):
        self.assertEqual(
            infer_worker_name({'INTEROVES_RUNTIME_ROLE': 'background-worker'}),
            'background',
        )

    def test_mode_aliases_are_normalized(self):
        config = load_worker_config(worker_name='background', mode='fargate-spot', environ={})
        self.assertEqual(config.mode, 'ecs-fargate-spot')

    def test_unsupported_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            load_worker_config(worker_name='integrations', mode='lambda', environ={})

    def test_required_env_names_are_reported_without_values(self):
        config = load_worker_config(worker_name='integrations', environ={})
        self.assertEqual(
            config.missing,
            ('TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'TELEGRAM_BOT_TOKEN'),
        )

    def test_recheck_compatibility_poller_does_not_require_hmac_secret(self):
        config = load_worker_config(worker_name='recheck', mode='ecs-fargate', environ={})
        self.assertEqual(config.missing, ())

    def test_strict_command_fails_without_secret_values(self):
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(CommandError):
            call_command('worker_config', 'check', '--worker', 'integrations', '--strict')
