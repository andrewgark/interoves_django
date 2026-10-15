"""Static tests for the Green bundle configuration drift guard."""

import importlib.util
import json
import tempfile
import zipfile
from pathlib import Path

from django.test import SimpleTestCase


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eb_config_manifest.py"
SPEC = importlib.util.spec_from_file_location("eb_config_manifest", SCRIPT)
MANIFEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MANIFEST)


class GreenBundleManifestTests(SimpleTestCase):
    commit = "a" * 40

    def _fixture(self, root):
        for relative in MANIFEST.REQUIRED_GREEN_HOOKS | MANIFEST.PROTECTED_GREEN_CONFIG:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("protected/current config\n")
        for relative in MANIFEST.REQUIRED_GREEN_APP_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("required app source\n")
        old = root / MANIFEST.OLD_BACKGROUND_HOOK
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("create_index_bg\n")
        (root / "interoves_django").mkdir(parents=True, exist_ok=True)
        (root / "interoves_django/deploy_version.txt").write_text(self.commit[:7] + "\n")

    def test_allowed_hook_removal_is_manifested_and_final_zip_is_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "stage"
            root.mkdir()
            self._fixture(root)
            baseline = Path(temporary) / "baseline.json"
            MANIFEST.snapshot(root, baseline)
            (root / MANIFEST.OLD_BACKGROUND_HOOK).unlink()
            output = Path(temporary) / "manifest.json"
            MANIFEST.write_manifest(root, baseline, output, MANIFEST.GREEN_ENV, self.commit)
            manifest = json.loads(output.read_text())
            self.assertEqual(manifest["source_commit"], self.commit)
            self.assertEqual(manifest["diff_from_live"][0]["action"], "removed")

            archive = Path(temporary) / "green.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for path in root.rglob("*"):
                    if path.is_file():
                        bundle.write(path, path.relative_to(root).as_posix())
            MANIFEST.verify_archive(archive, MANIFEST.GREEN_ENV, self.commit)

    def test_protected_live_configuration_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "stage"
            root.mkdir()
            self._fixture(root)
            baseline = Path(temporary) / "baseline.json"
            MANIFEST.snapshot(root, baseline)
            (root / MANIFEST.OLD_BACKGROUND_HOOK).unlink()
            (root / ".ebextensions/scaling.config").write_text("unexpected change\n")
            with self.assertRaisesRegex(ValueError, "Unexpected .platform/.ebextensions changes"):
                MANIFEST.verify_green(root, json.loads(baseline.read_text()), MANIFEST.files(root), self.commit)
