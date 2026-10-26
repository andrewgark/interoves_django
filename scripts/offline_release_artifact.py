#!/usr/bin/env python3
"""Build and verify deterministic, offline application source archives."""

import argparse
import hashlib
import json
import re
import stat
import subprocess
import zipfile
from pathlib import Path


MANIFEST_NAME = "release-manifest.json"
PROFILE_FILE = "interoves_django/release_profile.txt"
STATIC_RELEASE_ID_FILE = "interoves_django/static_release_id.txt"
REQUIRED_FILES = {
    "games/realtime/admission.py",
    "games/management/commands/audit_chain_task_state_completeness.py",
    "games/site/http/middleware/db_transient_response.py",
    "games/daily/timing.py",
    "games/site/views/daily/timing.py",
    "games/results/sql.py",
    "static/js/daily_solve_timer.js",
    "interoves_django/asgi.py",
    STATIC_RELEASE_ID_FILE,
    "Procfile",
}
OBSERVE_ONLY_REQUIRED_FILES = {
    "games/realtime/admission.py",
    "interoves_django/asgi.py",
    STATIC_RELEASE_ID_FILE,
    "Procfile",
}
RELEASE_PROFILES = {
    "full": REQUIRED_FILES,
    "observe-only": OBSERVE_ONLY_REQUIRED_FILES,
}
FORBIDDEN_PATH_PARTS = {".git", ".venv", "venv", "secrets", "__pycache__"}
SECRET_NAME = re.compile(r"(^\.env($|\.)|\.pem$|\.key$|credentials|secret.*\.json$)", re.I)
SECRET_CONTENT = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
)


def _files(root):
    root = root.resolve()
    entries = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        parts = Path(relative).parts
        if any(part in FORBIDDEN_PATH_PARTS for part in parts):
            continue
        if path.is_dir():
            continue
        if path.is_symlink():
            raise ValueError(f"Symlink is not allowed in release source: {relative}")
        if not path.is_file() or path.suffix in {".pyc", ".sqlite3"}:
            continue
        if SECRET_NAME.search(path.name):
            raise ValueError(f"Secret-like filename is not allowed: {relative}")
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in SECRET_CONTENT):
            raise ValueError(f"Secret marker found in release source: {relative}")
        entries[relative] = data
    return entries


def _manifest(source_sha, files, modes, release_profile="full"):
    if release_profile not in RELEASE_PROFILES:
        raise ValueError("Unknown release profile: " + str(release_profile))
    missing = sorted(RELEASE_PROFILES[release_profile] - files.keys())
    if missing:
        raise ValueError("Release source is missing required files: " + ", ".join(missing))
    old_hook = ".platform/hooks/postdeploy/02_background_migrations.sh"
    if old_hook in files:
        raise ValueError("Legacy per-instance DDL hook remains in release source.")
    return {
        "format": 3,
        "release_profile": release_profile,
        "source_commit": source_sha,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
        "modes": {name: int(modes[name]) for name in sorted(files)},
    }


def _candidate_version(source_sha, files, modes):
    digest = hashlib.sha256()
    digest.update(source_sha.encode())
    for name, data in sorted(files.items()):
        if name == "interoves_django/deploy_version.txt":
            continue
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
        digest.update(b"\0")
        digest.update(format(modes[name], "04o").encode())
    return digest.hexdigest()[:16]


def build(source, output, manifest_output, source_sha, release_profile="full"):
    source = Path(source)
    if release_profile not in RELEASE_PROFILES:
        raise ValueError("Unknown release profile: " + str(release_profile))
    profile_path = source / PROFILE_FILE
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(release_profile + "\n", encoding="utf-8")
    static_fingerprint_script = source / "scripts/static_source_fingerprint.sh"
    if static_fingerprint_script.is_file():
        static_release_id = subprocess.check_output(
            ["bash", str(static_fingerprint_script)], cwd=source, text=True
        ).strip()
    else:
        # Small synthetic test fixtures may not include the static source tree.
        static_release_id = hashlib.sha256(b"").hexdigest()
    static_release_file = source / STATIC_RELEASE_ID_FILE
    static_release_file.parent.mkdir(parents=True, exist_ok=True)
    static_release_file.write_text(static_release_id + "\n", encoding="utf-8")
    deploy_version = source / "interoves_django/deploy_version.txt"
    if (source / "interoves_django").is_dir():
        deploy_version.parent.mkdir(parents=True, exist_ok=True)
        initial_files = _files(source)
        initial_modes = {
            name: stat.S_IMODE((source / name).stat().st_mode)
            for name in initial_files
        }
        deploy_version.write_text(
            _candidate_version(source_sha, initial_files, initial_modes) + "\n"
        )
    files = _files(source)
    modes = {
        name: stat.S_IMODE((source / name).stat().st_mode)
        for name in files
    }
    manifest = _manifest(source_sha, files, modes, release_profile)
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | modes[name]) << 16
            archive.writestr(info, data)
        info = zipfile.ZipInfo(MANIFEST_NAME, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(info, manifest_bytes)
    Path(manifest_output).write_bytes(manifest_bytes)
    verify(output, source_sha, expected_profile=release_profile)
    print(f"files={len(files)}")
    print(f"archive_sha256={hashlib.sha256(output.read_bytes()).hexdigest()}")
    print(f"manifest={manifest_output}")


def verify(archive_path, expected_source_sha=None, expected_sha256=None, expected_profile=None):
    archive_path = Path(archive_path)
    if expected_sha256:
        digest = hashlib.sha256()
        with archive_path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest().lower() != expected_sha256.lower():
            raise ValueError('Archive SHA-256 does not match the approved value.')
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or MANIFEST_NAME not in names:
            raise ValueError("Archive has duplicate members or no release manifest.")
        manifest = json.loads(archive.read(MANIFEST_NAME))
        if expected_source_sha and manifest.get("source_commit") != expected_source_sha:
            raise ValueError("Archive source commit does not match expected SHA.")
        release_profile = manifest.get("release_profile", "full")
        if expected_profile and release_profile != expected_profile:
            raise ValueError("Archive release profile does not match expected profile.")
        actual = {name: archive.read(name) for name in names if name != MANIFEST_NAME}
        actual_modes = {
            info.filename: stat.S_IMODE(info.external_attr >> 16)
            for info in archive.infolist()
            if info.filename != MANIFEST_NAME
        }
        expected_names = set(manifest.get("files", {}))
        if set(actual) != expected_names:
            raise ValueError("Archive member list does not match its manifest.")
        expected = _manifest(
            manifest.get("source_commit", ""), actual, actual_modes, release_profile,
        )
        # Format 2 artifacts predate the profile field and remain verifiable as
        # full Phase 1 releases without weakening their member hash checks.
        if manifest.get("format") == 2 and "release_profile" not in manifest:
            expected["format"] = 2
            expected.pop("release_profile")
        if expected != manifest:
            raise ValueError("Archive content does not match its SHA-256 manifest.")
        if PROFILE_FILE in actual and actual[PROFILE_FILE].decode().strip() != release_profile:
            raise ValueError("Archive release profile marker does not match its manifest.")
        version_file = "interoves_django/deploy_version.txt"
        if version_file in actual:
            version = actual[version_file].decode().strip()
            if version != _candidate_version(manifest["source_commit"], actual, actual_modes):
                raise ValueError("Deployment version does not match archive contents.")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    builder = subparsers.add_parser("build")
    builder.add_argument("source")
    builder.add_argument("archive")
    builder.add_argument("manifest")
    builder.add_argument("--source-commit", required=True)
    builder.add_argument("--profile", choices=sorted(RELEASE_PROFILES), default="full")
    verifier = subparsers.add_parser("verify")
    verifier.add_argument("archive")
    verifier.add_argument("--source-commit")
    verifier.add_argument("--sha256")
    verifier.add_argument("--profile", choices=sorted(RELEASE_PROFILES))
    args = parser.parse_args()
    if args.command == "build":
        build(args.source, args.archive, args.manifest, args.source_commit, args.profile)
    else:
        manifest = verify(args.archive, args.source_commit, args.sha256, args.profile)
        print(f"verified_source_commit={manifest['source_commit']}")
        print(f"verified_release_profile={manifest.get('release_profile', 'full')}")
        print(f"verified_files={len(manifest['files'])}")


if __name__ == "__main__":
    main()
