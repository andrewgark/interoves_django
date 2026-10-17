import tempfile
import stat
import zipfile
import hashlib
from pathlib import Path

from django.test import SimpleTestCase

from scripts.eb_config_manifest import _archive_version_matches
from scripts.offline_release_artifact import (
    OBSERVE_ONLY_REQUIRED_FILES,
    PROFILE_FILE,
    REQUIRED_FILES,
    STATIC_RELEASE_ID_FILE,
    build,
    verify,
)


class OfflineReleaseArtifactTests(SimpleTestCase):
    def test_manifest_covers_all_members_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'
            root.mkdir()
            for relative in REQUIRED_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('candidate:' + relative)
            (root / '.platform/hooks/postdeploy').mkdir(parents=True)
            archive = Path(temp) / 'candidate.zip'
            manifest_path = Path(temp) / 'manifest.json'
            build(root, archive, manifest_path, 'a' * 40)
            manifest = verify(archive, 'a' * 40)
            self.assertEqual(
                set(manifest['files']),
                REQUIRED_FILES | {
                    'interoves_django/deploy_version.txt',
                    PROFILE_FILE,
                },
            )
            with zipfile.ZipFile(archive, 'a') as bundle:
                bundle.writestr('unexpected.txt', 'unreviewed')
            with self.assertRaisesRegex(ValueError, 'member list'):
                verify(archive)

    def test_manifest_requires_legacy_ddl_hook_to_be_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in REQUIRED_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('candidate')
            hook = root / '.platform/hooks/postdeploy/02_background_migrations.sh'
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text('unsafe')
            with self.assertRaisesRegex(ValueError, '[Ll]egacy per-instance DDL hook'):
                build(root, root / 'candidate.zip', root / 'manifest.json', 'b' * 40)

    def test_archive_preserves_executable_hook_modes_and_manifests_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'
            root.mkdir()
            for relative in REQUIRED_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('candidate:' + relative)
            hook = root / '.platform/hooks/postdeploy/probe.sh'
            hook.parent.mkdir(parents=True)
            hook.write_text('#!/bin/sh\nexit 0\n')
            hook.chmod(0o755)
            archive = Path(temp) / 'candidate.zip'
            manifest_path = Path(temp) / 'manifest.json'

            build(root, archive, manifest_path, 'c' * 40)

            manifest = verify(archive, 'c' * 40)
            self.assertEqual(manifest['modes']['.platform/hooks/postdeploy/probe.sh'], 0o755)
            with zipfile.ZipFile(archive) as bundle:
                mode = stat.S_IMODE(bundle.getinfo('.platform/hooks/postdeploy/probe.sh').external_attr >> 16)
            self.assertEqual(mode, 0o755)

    def test_eb_config_verifier_accepts_content_addressed_release_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'
            root.mkdir()
            for relative in REQUIRED_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('candidate:' + relative)
            archive = Path(temp) / 'candidate.zip'
            build(root, archive, Path(temp) / 'manifest.json', 'd' * 40)
            with zipfile.ZipFile(archive) as bundle:
                version = bundle.read('interoves_django/deploy_version.txt').decode().strip()
                static_id = bundle.read(STATIC_RELEASE_ID_FILE).decode().strip()
                self.assertEqual(len(static_id), 64)
                self.assertTrue(_archive_version_matches(bundle, version, 'd' * 40))
                self.assertFalse(_archive_version_matches(bundle, 'e' * 16, 'd' * 40))

    def test_verify_rejects_archive_hash_mismatch_before_reading_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / 'candidate.zip'
            archive.write_bytes(b'not a zip')
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                verify(archive, 'f' * 40, '0' * 64)

    def test_verify_accepts_exact_archive_hash_and_source_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'
            root.mkdir()
            for relative in REQUIRED_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('candidate:' + relative)
            archive = Path(temp) / 'candidate.zip'
            build(root, archive, Path(temp) / 'manifest.json', 'f' * 40)
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            verified = verify(archive, 'f' * 40, checksum)
            self.assertEqual(verified['source_commit'], 'f' * 40)
            with self.assertRaisesRegex(ValueError, 'source commit'):
                verify(archive, 'e' * 40, checksum)

    def test_observe_only_profile_does_not_require_timing_remediation_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'
            root.mkdir()
            for relative in OBSERVE_ONLY_REQUIRED_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('observe-only:' + relative)
            archive = Path(temp) / 'observe.zip'
            build(
                root, archive, Path(temp) / 'manifest.json', '1' * 40,
                release_profile='observe-only',
            )
            manifest = verify(archive, '1' * 40, expected_profile='observe-only')
            self.assertEqual(manifest['release_profile'], 'observe-only')
            self.assertEqual(manifest['files'][PROFILE_FILE], hashlib.sha256(b'observe-only\n').hexdigest())
            self.assertNotIn('games/middleware/db_transient_response.py', manifest['files'])
            with self.assertRaisesRegex(ValueError, 'release profile'):
                verify(archive, expected_profile='full')
