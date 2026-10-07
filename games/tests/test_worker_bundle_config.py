import json
from pathlib import Path
import unittest

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


ROOT = Path(__file__).resolve().parents[2]


class RecheckWorkerBundleConfigTests(unittest.TestCase):
    def test_ecs_worker_service_template_has_unique_yaml_mapping_keys(self):
        template = yaml.compose((ROOT / 'infra/ecs/worker-service.yaml').read_text())
        duplicates = []

        def inspect(node, path='$'):
            if isinstance(node, MappingNode):
                seen = set()
                for key, value in node.value:
                    key_text = key.value if isinstance(key, ScalarNode) else repr(key)
                    if key_text in seen:
                        duplicates.append(f'{path}.{key_text}')
                    seen.add(key_text)
                    inspect(value, f'{path}.{key_text}')
            elif isinstance(node, SequenceNode):
                for index, value in enumerate(node.value):
                    inspect(value, f'{path}[{index}]')

        inspect(template)
        self.assertEqual(duplicates, [], f'duplicate YAML mapping keys: {duplicates}')

    def test_ecs_apply_requires_an_explicit_desired_count(self):
        script = (ROOT / 'scripts' / 'deploy_ecs_worker.sh').read_text()
        self.assertIn('DESIRED_COUNT_SET=0', script)
        self.assertIn('Refusing ECS apply without --desired-count', script)

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
        hook = (ROOT / '.platform' / 'hooks' / 'postdeploy' / '06_install_playwright_chromium.sh').read_text()
        self.assertIn('interoves-integrations-worker', script)
        self.assertIn('playwright.config', script)
        self.assertIn('06_install_playwright_chromium.sh', script)
        self.assertIn('/home/app/.cache/ms-playwright', config)
        self.assertIn('python -m playwright install chromium', config)
        self.assertIn('python -m playwright install chromium &&', config)
        self.assertIn('PLAYWRIGHT_BROWSERS_PATH', hook)
        self.assertIn('chromium_headless_shell-*', hook)

    def test_web_bundle_injects_playwright_provisioning(self):
        script = (ROOT / 'scripts' / 'prepare_eb_bundle.sh').read_text()
        hook = (ROOT / '.platform' / 'hooks' / 'postdeploy' / '06_install_playwright_chromium.sh').read_text()
        self.assertIn('interoves-web-green-lb', script)
        self.assertIn('.ebextensions/playwright.config', script)
        self.assertIn('06_install_playwright_chromium.sh', script)
        self.assertIn('/home/webapp/.cache/ms-playwright', hook)
        self.assertIn('"$PYTHON" -m playwright install chromium', hook)

    def test_worker_image_contains_playwright_browser_for_integrations(self):
        dockerfile = (ROOT / 'Dockerfile.worker').read_text()
        self.assertIn('PLAYWRIGHT_BROWSERS_PATH=/home/app/.cache/ms-playwright', dockerfile)
        self.assertIn('python -m playwright install --with-deps chromium', dockerfile)
        self.assertIn('chown -R app:app /app "${PLAYWRIGHT_BROWSERS_PATH}"', dockerfile)

    def test_integrations_task_role_can_store_social_queue_images(self):
        template = (ROOT / 'infra/ecs/worker-task-role.yaml').read_text()
        script = (ROOT / 'scripts/deploy_ecs_worker.sh').read_text()
        self.assertIn('IsIntegrations', template)
        self.assertIn('s3:GetObject', template)
        self.assertIn('s3:PutObject', template)
        self.assertIn('s3:PutObjectAcl', template)
        self.assertIn('s3:ListBucket', template)
        self.assertIn('media/social_queue/*', template)
        self.assertIn('/media/social_queue/*', template)
        self.assertIn('SocialQueueS3Bucket="$SOCIAL_QUEUE_S3_BUCKET"', script)

    def test_web_playwright_install_does_not_hide_install_failure(self):
        config = (ROOT / '.ebextensions' / 'playwright.config').read_text()
        install_line = next(
            line for line in config.splitlines()
            if 'python -m playwright install chromium' in line
        )
        self.assertNotIn('chown -R webapp:webapp /home/webapp/.cache/ms-playwright /home/webapp/.fonts || true', install_line)
