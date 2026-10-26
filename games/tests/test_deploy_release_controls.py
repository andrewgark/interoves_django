import os
import json
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]


class DeployReleaseControlTests(SimpleTestCase):
    def test_static_publish_precedes_backend_rollout(self):
        script = (ROOT / 'scripts/deploy_green.sh').read_text()
        static_publish = script.index('scripts/publish_static.sh')
        update_environment = script.index('elasticbeanstalk update-environment')
        gate_enable = script.index('scripts/deploy_refresh_gate.sh" enable')
        backend_ready = script.index('wait_for_eb_deployment.sh')
        self.assertLess(static_publish, update_environment)
        self.assertLess(static_publish, gate_enable)
        self.assertLess(update_environment, backend_ready)
        self.assertIn('STATIC_RELEASE_ID="$release_static_id"', script)

    def test_static_release_is_immutable_and_fingerprint_is_release_scoped(self):
        settings = (ROOT / 'interoves_django/settings.py').read_text()
        storage = (ROOT / 'games/ops/health/storage.py').read_text()
        publisher = (ROOT / 'scripts/publish_static.sh').read_text()
        self.assertIn("STATIC_LOCATION = f'static/releases/{STATIC_RELEASE_ID}'", settings)
        self.assertIn('location = settings.STATIC_LOCATION', storage)
        self.assertIn('MARKER_KEY="${STATIC_PREFIX}/.source-fingerprint"', publisher)
        self.assertIn('STATIC_RELEASE_ID="$RELEASE_ID"', publisher)

    def test_static_publisher_skips_collectstatic_for_existing_release_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'scripts').mkdir()
            (root / 'scripts/static_source_fingerprint.sh').write_text('#!/bin/sh\nprintf fingerprint-abc\n')
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            aws = bin_dir / 'aws'
            aws.write_text(
                '#!/bin/sh\n'
                'printf "%s\\n" "$*" >> "$AWS_CALLS"\n'
                'if [ "$1 $2" = "s3api list-objects-v2" ]; then '
                'printf "%s" "$6"; exit 0; fi\n'
                'if [ "$1 $2" = "s3 cp" ]; then printf fingerprint-abc > "$4"; exit 0; fi\n'
                'exit 3\n'
            )
            aws.chmod(0o755)
            python = root / 'python'
            python.write_text('#!/bin/sh\nprintf called > "$COLLECTSTATIC_CALLED"\nexit 0\n')
            python.chmod(0o755)
            calls = root / 'aws-calls'
            called = root / 'collectstatic-called'
            env = {
                **os.environ,
                'PATH': f'{bin_dir}:{os.environ["PATH"]}',
                'AWS_CALLS': str(calls),
                'COLLECTSTATIC_CALLED': str(called),
                'PYTHON': str(python),
                'STATIC_RELEASE_ID': 'fingerprint-abc',
                'DEPLOY_SOURCE_DIR': str(root),
                'STATIC_AWS_PROFILE': 'test-profile',
            }
            result = subprocess.run(
                [str(ROOT / 'scripts/publish_static.sh')], cwd=ROOT,
                env=env, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('skipping collectstatic', result.stdout)
            self.assertFalse(called.exists())
            self.assertIn('static/releases/fingerprint-abc/.source-fingerprint', calls.read_text())

    def test_s3_access_denied_is_not_treated_as_missing_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'scripts').mkdir()
            (root / 'scripts/static_source_fingerprint.sh').write_text('#!/bin/sh\nprintf fingerprint-abc\n')
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            aws = bin_dir / 'aws'
            aws.write_text('#!/bin/sh\necho AccessDenied >&2\nexit 1\n')
            aws.chmod(0o755)
            python = root / 'python'
            python.write_text('#!/bin/sh\necho unexpectedly-called > "$COLLECTSTATIC_CALLED"\n')
            python.chmod(0o755)
            called = root / 'collectstatic-called'
            result = subprocess.run(
                [str(ROOT / 'scripts/publish_static.sh')], cwd=ROOT,
                env={
                    **os.environ,
                    'PATH': f'{bin_dir}:{os.environ["PATH"]}',
                    'COLLECTSTATIC_CALLED': str(called),
                    'PYTHON': str(python),
                    'STATIC_RELEASE_ID': 'fingerprint-abc',
                    'DEPLOY_SOURCE_DIR': str(root),
                }, text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('AccessDenied', result.stderr)
            self.assertFalse(called.exists())

    def test_collectstatic_putobject_failure_keeps_exit_failure_and_does_not_write_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'scripts').mkdir()
            (root / 'scripts/static_source_fingerprint.sh').write_text('#!/bin/sh\nprintf fingerprint-abc\n')
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            aws = bin_dir / 'aws'
            aws.write_text(
                '#!/bin/sh\n'
                'if [ "$1 $2" = "s3api list-objects-v2" ]; then echo None; exit 0; fi\n'
                'echo unexpected-aws-call >&2; exit 1\n'
            )
            aws.chmod(0o755)
            python = root / 'python'
            python.write_text('#!/bin/sh\necho "AccessDenied: s3:PutObject" >&2\nexit 1\n')
            python.chmod(0o755)
            result = subprocess.run(
                [str(ROOT / 'scripts/publish_static.sh')], cwd=ROOT,
                env={
                    **os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}',
                    'PYTHON': str(python), 'STATIC_RELEASE_ID': 'fingerprint-abc',
                    'DEPLOY_SOURCE_DIR': str(root),
                }, text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('AccessDenied: s3:PutObject', result.stderr)

    def test_observe_only_refuses_when_immutable_static_release_is_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'scripts').mkdir()
            (root / 'scripts/static_source_fingerprint.sh').write_text('#!/bin/sh\nprintf fingerprint-abc\n')
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            aws = bin_dir / 'aws'
            aws.write_text('#!/bin/sh\necho None\n')
            aws.chmod(0o755)
            python = root / 'python'
            python.write_text('#!/bin/sh\necho unexpectedly-called >&2\nexit 1\n')
            python.chmod(0o755)
            result = subprocess.run(
                [str(ROOT / 'scripts/publish_static.sh'), '--verify-only'], cwd=ROOT,
                env={
                    **os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}',
                    'PYTHON': str(python), 'STATIC_RELEASE_ID': 'fingerprint-abc',
                    'DEPLOY_SOURCE_DIR': str(root),
                }, text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('refusing observe-only rollout', result.stderr)
            self.assertNotIn('unexpectedly-called', result.stderr)

    def test_observe_only_prebuilt_artifact_skips_static_publication(self):
        script = (ROOT / 'scripts/deploy_green.sh').read_text()
        profile = script.index('RELEASE_PROFILE="$(unzip -p')
        static_branch = script.index('if [[ "$RELEASE_PROFILE" == "observe-only" ]]')
        skip_message = script.index('skipping static publication')
        verify_call = script.index('scripts/publish_static.sh" --verify-only')
        self.assertLess(profile, static_branch)
        self.assertLess(static_branch, verify_call)
        self.assertLess(verify_call, skip_message)
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

    def test_deploy_gate_cleanup_runs_after_post_rollout_failure(self):
        self._run_gate_cleanup_harness(mark_backend_ready=True, fail=True)

    def test_deploy_gate_cleanup_runs_on_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_mock_gate(root)
            workdir = root / 'work'
            workdir.mkdir()
            harness = root / 'harness.sh'
            harness.write_text(
                'set -euo pipefail\n'
                f'source "{ROOT / "scripts/deploy_gate_cleanup.sh"}"\n'
                f'deploy_gate_cleanup_install "{root}" "{workdir}"\n'
                'deploy_gate_cleanup_arm\n'
                'deploy_gate_cleanup_update_requested\n'
                'sleep 30\n'
            )
            proc = subprocess.Popen(
                ['bash', str(harness)], cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                time.sleep(0.2)
                os.killpg(proc.pid, signal.SIGTERM)
                stdout, stderr = proc.communicate(timeout=5)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            self.assertEqual(proc.returncode, 143, (stdout, stderr))
            self.assertIn('DEPLOY STATUS UNCERTAIN', stderr)
            self.assertEqual((root / 'gate-disabled').read_text(), 'disabled')
            self.assertFalse(workdir.exists())

    def test_deploy_gate_cleanup_allows_immediate_retry_after_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_mock_gate(root)
            workdir = root / 'work'
            for _ in range(2):
                harness = root / 'harness.sh'
                harness.write_text(
                    'set -euo pipefail\n'
                    f'source "{ROOT / "scripts/deploy_gate_cleanup.sh"}"\n'
                    f'deploy_gate_cleanup_install "{root}" "{workdir}"\n'
                    'deploy_gate_cleanup_arm\n'
                    'exit 17\n'
                )
                result = subprocess.run(['bash', str(harness)], cwd=ROOT, text=True, capture_output=True)
                self.assertEqual(result.returncode, 17)
                self.assertEqual((root / 'gate-disabled').read_text(), 'disabled')
                (root / 'gate-disabled').unlink()

    def test_cloudwatch_observability_candidate_is_valid_and_bounded(self):
        path = ROOT / 'infra/elasticbeanstalk/future/cloudwatch/web-observability.json'
        config = json.loads(path.read_text())
        self.assertEqual(config['metrics']['namespace'], 'InterOves/Web')
        self.assertEqual(config['metrics']['append_dimensions']['InstanceId'], '${aws:InstanceId}')
        self.assertEqual(len(config['metrics']['metrics_collected']['procstat']), 3)
        journal = config['logs']['logs_collected']['journald']['collect_list']
        self.assertIn('web.service', journal[0]['units'])
        self.assertEqual(journal[0]['retention_in_days'], 3)
        self.assertEqual(journal[1]['matches'], [{'_TRANSPORT': 'kernel'}])

    def test_green_cloudwatch_agent_uses_file_input_for_journal_streams(self):
        config = json.loads((
            ROOT / 'infra/elasticbeanstalk/green/cloudwatch/web-observability.json'
        ).read_text())
        logs = config['logs']['logs_collected']['files']['collect_list']
        self.assertEqual(
            {item['file_path'] for item in logs},
            {'/var/log/interoves/kernel.log', '/var/log/interoves/systemd.log'},
        )
        self.assertEqual(len({item['log_group_name'] for item in logs}), 2)
        hook = (ROOT / '.platform/hooks/postdeploy/05_configure_cloudwatch_agent.sh').read_text()
        self.assertIn('journalctl --follow', hook)
        self.assertIn('interoves-kernel-journal.service', hook)
        self.assertIn('interoves-systemd-journal.service', hook)

    def _write_mock_gate(self, root):
        scripts = root / 'scripts'
        scripts.mkdir(exist_ok=True)
        gate = scripts / 'deploy_refresh_gate.sh'
        gate.write_text('#!/bin/sh\nprintf disabled > "$(dirname "$0")/../gate-disabled"\n')
        gate.chmod(0o755)

    def _run_gate_cleanup_harness(self, *, mark_backend_ready, fail):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_mock_gate(root)
            workdir = root / 'work'
            workdir.mkdir()
            harness = root / 'harness.sh'
            lines = [
                'set -euo pipefail',
                f'source "{ROOT / "scripts/deploy_gate_cleanup.sh"}"',
                f'deploy_gate_cleanup_install "{root}" "{workdir}"',
                'deploy_gate_cleanup_arm',
            ]
            if mark_backend_ready:
                lines.append('deploy_gate_cleanup_backend_ready')
            lines.append('exit 17' if fail else 'exit 0')
            harness.write_text('\n'.join(lines) + '\n')
            result = subprocess.run(['bash', str(harness)], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 17 if fail else 0)
            self.assertEqual((root / 'gate-disabled').read_text(), 'disabled')
            self.assertFalse(workdir.exists())
            if mark_backend_ready and fail:
                self.assertIn('PARTIAL DEPLOY: EB backend rollout succeeded', result.stderr)
