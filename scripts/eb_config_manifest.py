#!/usr/bin/env python
"""Snapshot and verify EB platform configuration carried in a source bundle."""

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path


CONFIG_DIRS = (".platform", ".ebextensions")
GREEN_ENV = "interoves-web-green-lb"
OLD_BACKGROUND_HOOK = ".platform/hooks/postdeploy/02_background_migrations.sh"
ALLOWED_GREEN_CHANGES = {
    OLD_BACKGROUND_HOOK,
    ".platform/hooks/postdeploy/06_install_playwright_chromium.sh",
    ".ebextensions/playwright.config",
}
REQUIRED_GREEN_HOOKS = {
    ".platform/hooks/postdeploy/00_cleanup_cron_backups.sh",
    ".platform/hooks/postdeploy/01_enable_ssm.sh",
    ".platform/hooks/postdeploy/03_sync_nutrimatic_index.sh",
    ".platform/hooks/postdeploy/04_background_worker_no_web_cron.sh",
    ".platform/hooks/postdeploy/05_configure_cloudwatch_agent.sh",
    ".platform/hooks/postdeploy/06_install_playwright_chromium.sh",
}
PROTECTED_GREEN_CONFIG = {
    ".ebextensions/django.config",
    ".ebextensions/deploy.config",
    ".ebextensions/health.config",
    ".ebextensions/scaling.config",
}
REQUIRED_GREEN_APP_FILES = {
    "games/management/commands/run_deploy_background_work.py",
    "games/management/commands/_background_index_executor.py",
    "games/management/commands/ensure_games_0109_indexes.py",
    "games/management/commands/ensure_registration_indexes.py",
    "games/management/commands/backfill_chain_task_states.py",
}


def files(root):
    result = {}
    for directory in CONFIG_DIRS:
        base = root / directory
        if not base.exists():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            relative = path.relative_to(root).as_posix()
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def snapshot(root, output):
    output.write_text(json.dumps(files(root), indent=2, sort_keys=True) + "\n")


def verify_green(root, before, current, commit):
    changed = {
        path for path in set(before) | set(current)
        if before.get(path) != current.get(path)
    }
    unexpected = changed - ALLOWED_GREEN_CHANGES
    if unexpected:
        raise ValueError("Unexpected .platform/.ebextensions changes: " + ", ".join(sorted(unexpected)))
    if OLD_BACKGROUND_HOOK in current:
        raise ValueError("Legacy detached background DDL hook remains in Green bundle.")
    for relative in REQUIRED_GREEN_HOOKS | PROTECTED_GREEN_CONFIG:
        if relative not in current:
            raise ValueError("Required live configuration is missing: " + relative)
    for relative in PROTECTED_GREEN_CONFIG:
        if before.get(relative) != current.get(relative):
            raise ValueError("Protected Green capacity/health/deploy config changed: " + relative)
    for relative in REQUIRED_GREEN_APP_FILES:
        if not (root / relative).is_file():
            raise ValueError("Controlled deployment executor file is missing from bundle: " + relative)
    deploy_version = root / "interoves_django/deploy_version.txt"
    expected_deploy_version = commit[:7]
    if not deploy_version.is_file() or deploy_version.read_text().strip() != expected_deploy_version:
        raise ValueError("Bundle deploy_version does not match the source commit.")
    hook_root = root / ".platform/hooks"
    for path in hook_root.rglob("*") if hook_root.exists() else ():
        if path.is_file():
            content = path.read_text(errors="replace")
            if "create_index_bg" in content or "INDEXES_ATTEMPT" in content:
                raise ValueError("Legacy background index executor found in " + path.relative_to(root).as_posix())
    return changed


def write_manifest(root, baseline, output, environment, commit):
    if environment == GREEN_ENV and not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Green manifest requires the full 40-character Git commit SHA.")
    before = json.loads(baseline.read_text())
    current = files(root)
    changed = set()
    if environment == GREEN_ENV:
        changed = verify_green(root, before, current, commit)
    manifest = {
        "format": 1,
        "environment": environment,
        "source_commit": commit,
        "configuration": [
            {
                "path": path,
                "sha256": digest,
                "source": "inherited-live-bundle" if path not in changed else "explicit-green-overlay",
            }
            for path, digest in sorted(current.items())
        ],
        "diff_from_live": [
            {
                "path": path,
                "before_sha256": before.get(path),
                "after_sha256": current.get(path),
                "action": "removed" if path in before and path not in current else (
                    "added" if path not in before else "modified"
                ),
            }
            for path in sorted(changed)
        ],
    }
    (root / "interoves_eb_config_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def verify_archive(archive, environment, commit):
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        manifest_name = "interoves_eb_config_manifest.json"
        if environment == GREEN_ENV and manifest_name not in names:
            raise ValueError("Green ZIP is missing its configuration manifest.")
        if manifest_name not in names:
            return
        manifest = json.loads(bundle.read(manifest_name))
        if manifest.get("environment") != environment:
            raise ValueError("ZIP manifest environment does not match target.")
        if manifest.get("source_commit") != commit:
            raise ValueError("ZIP manifest commit does not match source commit.")
        if environment == GREEN_ENV and not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Green ZIP verification requires the full Git commit SHA.")
        for item in manifest.get("configuration", []):
            path = item["path"]
            if path not in names:
                raise ValueError("ZIP is missing manifested configuration: " + path)
            digest = hashlib.sha256(bundle.read(path)).hexdigest()
            if digest != item["sha256"]:
                raise ValueError("ZIP configuration hash mismatch: " + path)
        if environment == GREEN_ENV:
            if OLD_BACKGROUND_HOOK in names:
                raise ValueError("Legacy detached background DDL hook remains in Green ZIP.")
            required = REQUIRED_GREEN_HOOKS | PROTECTED_GREEN_CONFIG | REQUIRED_GREEN_APP_FILES
            missing = required - names
            if missing:
                raise ValueError("Green ZIP is missing required files: " + ", ".join(sorted(missing)))
            version = bundle.read("interoves_django/deploy_version.txt").decode().strip()
            if version != commit[:7]:
                raise ValueError("Green ZIP deploy version does not match commit SHA.")
            for path in names:
                if path.startswith(".platform/hooks/") and not path.endswith("/"):
                    content = bundle.read(path).decode(errors="replace")
                    if "create_index_bg" in content or "INDEXES_ATTEMPT" in content:
                        raise ValueError("Legacy background index executor in ZIP: " + path)


def main(argv):
    if len(argv) < 2:
        raise ValueError(
            "usage: eb_config_manifest.py snapshot ROOT OUTPUT | "
            "write ROOT BASELINE OUTPUT ENV COMMIT | verify-zip ZIP ENV COMMIT"
        )
    action = argv[1]
    if action == "snapshot" and len(argv) == 4:
        snapshot(Path(argv[2]), Path(argv[3]))
        return
    if action == "write" and len(argv) == 7:
        write_manifest(Path(argv[2]), Path(argv[3]), Path(argv[4]), argv[5], argv[6])
        return
    if action == "verify-zip" and len(argv) == 5:
        verify_archive(Path(argv[2]), argv[3], argv[4])
        return
    raise ValueError("invalid arguments")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"EB config manifest check failed: {exc}", file=sys.stderr)
        sys.exit(1)
