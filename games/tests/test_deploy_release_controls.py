import os
import subprocess
import tempfile
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]


class DeployReleaseControlTests(SimpleTestCase):
    def test_static_publish_follows_backend_health_gate(self):
        script = (ROOT / 'scripts/deploy_green.sh').read_text()
        backend_ready = script.index('wait_for_eb_deployment.sh')
        static_publish = script.index('scripts/publish_static.sh')
        self.assertLess(backend_ready, static_publish)

    def test_observe_only_prebuilt_artifact_skips_static_publication(self):
        script = (ROOT / 'scripts/deploy_green.sh').read_text()
        profile = script.index('RELEASE_PROFILE="$(unzip -p')
        static_branch = script.index('if [[ "$RELEASE_PROFILE" == "observe-only" ]]')
        skip_message = script.index('skipping static publication')
        publish_call = script.index('scripts/publish_static.sh')
        self.assertLess(profile, static_branch)
        self.assertLess(static_branch, skip_message)
        self.assertLess(skip_message, publish_call)
        self.assertIn('offline_release_artifact.py" verify', script)

    def test_schema_work_is_opt_in_and_defaults_off(self):
        script = (ROOT / 'scripts/deploy_green.sh').read_text()
        self.assertIn('case "${ENABLE_POSTDEPLOY_SCHEMA_WORK:-0}"', script)
        self.assertIn('0) echo "Skipping deferred schema work', script)
        self.assertLess(
            script.index('case "${ENABLE_POSTDEPLOY_SCHEMA_WORK:-0}"'),
            script.index('manage.py run_deploy_background_work'),
        )

    def test_green_bundle_removes_legacy_per_instance_ddl_hook(self):
        script = (ROOT / 'scripts/prepare_eb_bundle.sh').read_text()
        green_branch = script.index('if [[ "$ENV_NAME" == "interoves-web-green-lb" ]]')
        hook_removal = script.index('rm -f "$stage/.platform/hooks/postdeploy/02_background_migrations.sh"')
        self.assertLess(green_branch, hook_removal)

    def test_deploy_driver_accepts_verified_immutable_artifact(self):
        script = (ROOT / 'deploy.sh').read_text()
        verify = script.index('offline_release_artifact.py" verify')
        member_check = script.index('unzip -Z1 "$PREBUILT_RELEASE_ARTIFACT"')
        git_archive = script.index('git archive "$SOURCE_SHA"')
        self.assertLess(verify, member_check)
        self.assertLess(member_check, git_archive)
        self.assertIn('immutable prebuilt artifact', script)

    def test_exact_artifact_mode_requires_explicit_hash_commit_and_gate_off(self):
        script = (ROOT / 'deploy.sh').read_text()
        for option in ('--prebuilt-artifact', '--sha256', '--source-commit'):
            self.assertIn(option, script)
        self.assertIn('Archive SHA-256', (ROOT / 'scripts/offline_release_artifact.py').read_text())
        self.assertIn('ENABLE_POSTDEPLOY_SCHEMA_WORK=0', script)

    def test_exact_artifact_dry_run_rejects_bad_hash_before_deploy_driver(self):
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp) / 'bad.zip'
            artifact.write_bytes(b'not the approved release')
            result = subprocess.run(
                [
                    str(ROOT / 'deploy.sh'), '--prebuilt-artifact', str(artifact),
                    '--sha256', '0' * 64, '--source-commit', '6e30f327965fbb9af32c15f3bd80850f6104ba4b',
                    '--dry-run',
                ],
                cwd=ROOT, text=True, capture_output=True,
                env={**os.environ, 'ENABLE_POSTDEPLOY_SCHEMA_WORK': '0'},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('SHA-256 does not match', result.stderr)
            self.assertNotIn('Dry run: target would be', result.stdout)

    def test_exact_artifact_rejects_enabled_schema_executor_before_inspecting_artifact(self):
        result = subprocess.run(
            [
                str(ROOT / 'deploy.sh'), '--prebuilt-artifact', '/does/not/exist.zip',
                '--sha256', '0' * 64, '--source-commit', '6e30f327965fbb9af32c15f3bd80850f6104ba4b',
                '--dry-run',
            ],
            cwd=ROOT, text=True, capture_output=True,
            env={**os.environ, 'ENABLE_POSTDEPLOY_SCHEMA_WORK': '1'},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires ENABLE_POSTDEPLOY_SCHEMA_WORK=0', result.stderr)
        self.assertNotIn('Prebuilt release artifact not found', result.stderr)

    def test_exact_artifact_dry_run_has_no_packaging_or_static_publish_path(self):
        script = (ROOT / 'scripts/deploy_green.sh').read_text()
        exact_branch = script.index('if [[ -n "${PREBUILT_RELEASE_ARTIFACT:-}" ]]')
        dry_run_exit = script.index('if [[ "$DO_DEPLOY" == "0" ]]')
        publish_call = script.index('scripts/publish_static.sh')
        aws_bootstrap = script.index('source "$ROOT/scripts/interoves_aws_bootstrap.sh"')
        prebuilt_branch_end = script.index('\nelse\n', exact_branch)
        self.assertLess(exact_branch, dry_run_exit)
        self.assertLess(dry_run_exit, aws_bootstrap)
        self.assertLess(dry_run_exit, publish_call)
        self.assertNotIn('scripts/prepare_eb_bundle.sh', script[exact_branch:prebuilt_branch_end])
        self.assertIn('scripts/prepare_eb_bundle.sh', script[prebuilt_branch_end:])
        self.assertLess(dry_run_exit, script.index('unzip -q "$bundle" -d "$release_source"'))
