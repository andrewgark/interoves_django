import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class RecheckWorkerBundleConfigTests(unittest.TestCase):
    def test_legacy_word_salad_worker_is_not_a_deploy_target(self):
        script = (ROOT / 'scripts' / 'deploy_worker.sh').read_text()
        self.assertNotIn('interoves-word-salad-worker|', script)

    def test_recheck_deploy_has_schema_preflight(self):
        script = (ROOT / 'scripts' / 'deploy_worker.sh').read_text()
        preflight = (ROOT / 'scripts' / 'recheck_preflight.sh').read_text()
        self.assertIn('recheck_preflight.sh', script)
        self.assertIn('showmigrations games --plan', preflight)
        self.assertIn('dispatch_recheck_outbox_loop.py', preflight)

    def test_packaging_includes_dedicated_worker_hooks(self):
        script = (ROOT / 'scripts' / 'prepare_eb_bundle.sh').read_text()
        self.assertIn('interoves-recheck-worker', script)
        self.assertIn('09_populate_recheck_secret.sh', script)
        self.assertIn('10_enable_word_salad_dispatcher.sh', script)
        self.assertIn('.platform/recheck-worker.marker', script)

    def test_worker_template_has_django_secret_and_dispatcher_permissions(self):
        options = (ROOT / 'infra/elasticbeanstalk/future/worker/option-settings.config').read_text()
        self.assertIn('DJANGO_SECRET_KEY: __DJANGO_SECRET_KEY_ARN__', options)

        policy = json.loads(
            (ROOT / 'infra/elasticbeanstalk/future/worker/worker-instance-iam-policy.json').read_text()
        )
        statements = {statement['Sid']: statement for statement in policy['Statement']}
        self.assertIn('sqs:SendMessage', statements['ReceiveRecheckMessages']['Action'])
        self.assertIn('__DJANGO_SECRET_KEY_ARN__', statements['WorkerSecrets']['Resource'])

    def test_integrations_bundle_injects_playwright_provisioning(self):
        script = (ROOT / 'scripts' / 'prepare_eb_bundle.sh').read_text()
        config = (ROOT / 'infra' / 'elasticbeanstalk' / 'future' / 'integrations-worker' / 'playwright.config').read_text()
        self.assertIn('interoves-integrations-worker', script)
        self.assertIn('playwright.config', script)
        self.assertIn('/home/app/.cache/ms-playwright', config)
        self.assertIn('python -m playwright install chromium', config)
        self.assertIn('python -m playwright install chromium &&', config)

    def test_web_playwright_install_does_not_hide_install_failure(self):
        config = (ROOT / '.ebextensions' / 'playwright.config').read_text()
        install_line = next(
            line for line in config.splitlines()
            if 'python -m playwright install chromium' in line
        )
        self.assertNotIn('chown -R webapp:webapp /home/webapp/.cache/ms-playwright /home/webapp/.fonts || true', install_line)
