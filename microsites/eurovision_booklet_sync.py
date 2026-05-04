"""
Sync Eurovision booklet PDFs from git (local clone or GitHub API).

See EUROVISION_BOOKLET_* settings. Cached files and manifest live under
BASE_DIR/var/eurovision_booklet/2026/ (overridable).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# All booklet PDFs (10). Some pages may reference a subset; sync keeps the full set fresh.
BOOKLET_PDF_FILENAMES: tuple[str, ...] = (
    "eurovision2026_en.pdf",
    "eurovision2026_final_en.pdf",
    "eurovision2026_final_ru.pdf",
    "eurovision2026_results_en.pdf",
    "eurovision2026_results_ru.pdf",
    "eurovision2026_ru.pdf",
    "eurovision2026_sf1_en.pdf",
    "eurovision2026_sf1_ru.pdf",
    "eurovision2026_sf2_en.pdf",
    "eurovision2026_sf2_ru.pdf",
)

# Sections on the public minisite (subset of BOOKLET_PDF_FILENAMES).
BOOKLET_MINISITE_SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "overall-pre",
        "All countries",
        "eurovision2026_ru.pdf",
        "eurovision2026_en.pdf",
    ),
    (
        "semi-1",
        "Semi-final 1",
        "eurovision2026_sf1_ru.pdf",
        "eurovision2026_sf1_en.pdf",
    ),
    (
        "semi-2",
        "Semi-final 2",
        "eurovision2026_sf2_ru.pdf",
        "eurovision2026_sf2_en.pdf",
    ),
)


@dataclass(frozen=True)
class PdfMeta:
    commit: str
    commit_date: datetime


def _cache_root(base_dir: Path) -> Path:
    return base_dir / "var" / "eurovision_booklet" / "2026"


def _manifest_path(base_dir: Path) -> Path:
    return _cache_root(base_dir) / "manifest.json"


def _lock_path(base_dir: Path) -> Path:
    return _cache_root(base_dir) / ".sync.lock"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=".manifest-",
        delete=False,
    ) as tf:
        tmp_name = tf.name
        tf.write(raw)
    os.replace(tmp_name, path)


def _parse_git_datetime(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _github_request(
    url: str,
    token: str | None,
    timeout: float,
) -> tuple[int, bytes]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "interoves-django-booklet"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        return resp.getcode(), resp.read()


def _sync_github(
    *,
    owner: str,
    repo: str,
    branch: str,
    dist_path: str,
    token: str | None,
    cache_dir: Path,
    manifest: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    base_api = f"https://api.github.com/repos/{owner}/{repo}"
    _, body = _github_request(f"{base_api}/branches/{quote(branch)}", token, timeout)
    tip = json.loads(body.decode("utf-8"))["commit"]["sha"]
    old_tip = (manifest.get("branch_tip") or "").strip()

    if old_tip == tip:
        manifest.setdefault("branch_tip", tip)
        return manifest

    changed: set[str]
    if not old_tip:
        changed = set(BOOKLET_PDF_FILENAMES)
    else:
        compare_url = f"{base_api}/compare/{old_tip}...{tip}"
        try:
            _, cbody = _github_request(compare_url, token, timeout)
            cmp = json.loads(cbody.decode("utf-8"))
            status = cmp.get("status")
            if status not in ("ahead", "diverged", "identical"):
                logger.warning(
                    "Eurovision booklet: unexpected compare status %s; refreshing all PDFs",
                    status,
                )
                changed = set(BOOKLET_PDF_FILENAMES)
            else:
                names = set()
                for f in cmp.get("files") or []:
                    fn = f.get("filename") or ""
                    if fn.lower().endswith(".pdf"):
                        names.add(Path(fn).name)
                changed = names & set(BOOKLET_PDF_FILENAMES)
        except HTTPError as e:
            if e.code == 404:
                logger.warning(
                    "Eurovision booklet: compare %s...%s not found; full refresh",
                    old_tip[:7],
                    tip[:7],
                )
                changed = set(BOOKLET_PDF_FILENAMES)
            else:
                raise

    if old_tip and not changed:
        manifest["branch_tip"] = tip
        manifest["backend"] = "github"
        manifest["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return manifest

    files_meta: dict[str, Any] = dict(manifest.get("files") or {})

    for name in BOOKLET_PDF_FILENAMES:
        if name not in changed:
            continue
        rel = f"{dist_path.strip().strip('/')}/{name}"
        path_query = quote(rel, safe="")
        hist_url = f"{base_api}/commits?path={path_query}&sha={tip}&per_page=1"
        try:
            _, hbody = _github_request(hist_url, token, timeout)
            commits = json.loads(hbody.decode("utf-8"))
        except HTTPError as e:
            if e.code == 404:
                logger.info("Eurovision booklet: no history for %s — skipping", name)
                continue
            raise
        if not commits:
            logger.info("Eurovision booklet: %s not in repo at tip — skipping", name)
            continue
        c0 = commits[0]
        sha = c0["sha"]
        date_s = c0["commit"]["committer"]["date"]
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{tip}/{rel}"
        try:
            _, pdf_bytes = _github_request(raw_url, token, timeout)
        except HTTPError as e:
            if e.code == 404:
                logger.info("Eurovision booklet: raw %s missing — skipping", name)
                continue
            raise
        out = cache_dir / name
        out.write_bytes(pdf_bytes)
        files_meta[name] = {"commit": sha, "commit_date": date_s}
        logger.info("Eurovision booklet: updated %s from GitHub", name)

    manifest["branch_tip"] = tip
    manifest["backend"] = "github"
    manifest["files"] = files_meta
    manifest["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return manifest


def _git_run(
    repo: Path,
    args: list[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _sync_local_git(
    *,
    repo: Path,
    remote: str,
    branch: str,
    dist_path: str,
    cache_dir: Path,
    manifest: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    dist_path = dist_path.strip().strip("/")
    fetch = _git_run(repo, ["fetch", remote, branch], timeout=timeout)
    if fetch.returncode != 0:
        msg = (fetch.stderr or fetch.stdout or "").strip()
        raise RuntimeError(f"git fetch failed: {msg}")

    tip_proc = _git_run(
        repo,
        ["rev-parse", f"{remote}/{branch}"],
        timeout=timeout,
    )
    if tip_proc.returncode != 0:
        raise RuntimeError(
            f"git rev-parse failed: {(tip_proc.stderr or '').strip()}"
        )
    tip = tip_proc.stdout.strip()
    old_tip = (manifest.get("branch_tip") or "").strip()

    if old_tip == tip:
        manifest.setdefault("branch_tip", tip)
        return manifest

    if not old_tip:
        changed = set(BOOKLET_PDF_FILENAMES)
    else:
        diff = _git_run(
            repo,
            ["diff", "--name-only", old_tip, tip, "--", dist_path],
            timeout=timeout,
        )
        if diff.returncode != 0:
            logger.warning(
                "Eurovision booklet: git diff %s..%s failed; refreshing all PDFs",
                old_tip[:7],
                tip[:7],
            )
            changed = set(BOOKLET_PDF_FILENAMES)
        else:
            changed = set()
            for line in diff.stdout.splitlines():
                line = line.strip()
                if line.lower().endswith(".pdf"):
                    changed.add(Path(line).name)
            changed &= set(BOOKLET_PDF_FILENAMES)

    if old_tip and not changed:
        manifest["branch_tip"] = tip
        manifest["backend"] = "git_local"
        manifest["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return manifest

    files_meta: dict[str, Any] = dict(manifest.get("files") or {})

    for name in BOOKLET_PDF_FILENAMES:
        if name not in changed:
            continue
        rel = f"{dist_path}/{name}"
        show = subprocess.run(
            ["git", "-C", str(repo), "show", f"{tip}:{rel}"],
            capture_output=True,
            timeout=min(timeout, 120.0),
        )
        if show.returncode != 0:
            logger.info(
                "Eurovision booklet: %s not available at %s — skipping",
                rel,
                tip[:7],
            )
            continue
        (cache_dir / name).write_bytes(show.stdout)

        logp = _git_run(
            repo,
            ["log", "-1", "--format=%H %cI", tip, "--", rel],
            timeout=timeout,
        )
        if logp.returncode != 0 or not logp.stdout.strip():
            continue
        parts = logp.stdout.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        sha, date_s = parts
        files_meta[name] = {"commit": sha, "commit_date": date_s}
        logger.info("Eurovision booklet: updated %s from local git", name)

    manifest["branch_tip"] = tip
    manifest["backend"] = "git_local"
    manifest["files"] = files_meta
    manifest["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return manifest


def sync_configured(settings) -> bool:
    gh = (getattr(settings, "EUROVISION_BOOKLET_GITHUB_REPO", None) or "").strip()
    loc = (getattr(settings, "EUROVISION_BOOKLET_REPO_PATH", None) or "").strip()
    return bool(gh or loc)


def should_skip_throttle(settings) -> bool:
    """Use Django cache so throttling works across workers."""
    interval = int(getattr(settings, "EUROVISION_BOOKLET_SYNC_MIN_INTERVAL_SEC", 120))
    if interval <= 0:
        return False
    try:
        from django.core.cache import cache
    except Exception:
        return False
    # add() returns True only if the key did not exist.
    return not cache.add("eurovision_booklet:sync_cooldown", 1, timeout=interval)


def ensure_eurovision_booklet_sync(settings) -> dict[str, Any]:
    """
    Update cached PDFs if the remote branch tip changed; refresh only PDFs that
    differ between the previous tip and the current tip.

    Returns the manifest dict (possibly unchanged). On error, returns the last
    known manifest or {}.
    """
    if not sync_configured(settings):
        return {}

    base_dir = Path(settings.BASE_DIR)
    cache_dir = _cache_root(base_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(base_dir)
    manifest = _read_json(manifest_path)
    if should_skip_throttle(settings):
        return _read_json(manifest_path)

    timeout = float(getattr(settings, "EUROVISION_BOOKLET_HTTP_TIMEOUT", 60.0))
    dist_path = (
        getattr(settings, "EUROVISION_BOOKLET_DIST_PATH", None) or "dist"
    ).strip()

    lock = _lock_path(base_dir)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        fd = -1

    if fd >= 0:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            pass

    try:
        repo_path = (getattr(settings, "EUROVISION_BOOKLET_REPO_PATH", None) or "").strip()
        if repo_path:
            repo = Path(os.path.expanduser(repo_path))
            if not (repo / ".git").is_dir():
                logger.warning(
                    "Eurovision booklet: EUROVISION_BOOKLET_REPO_PATH is not a git repo: %s",
                    repo,
                )
                return manifest
            remote = (getattr(settings, "EUROVISION_BOOKLET_GIT_REMOTE", None) or "origin").strip()
            branch = (
                getattr(settings, "EUROVISION_BOOKLET_GIT_BRANCH", None) or "main"
            ).strip()
            try:
                manifest = _sync_local_git(
                    repo=repo,
                    remote=remote,
                    branch=branch,
                    dist_path=dist_path,
                    cache_dir=cache_dir,
                    manifest=manifest,
                    timeout=timeout,
                )
            except Exception:
                logger.exception("Eurovision booklet: local git sync failed")
                return _read_json(manifest_path) or manifest
        else:
            gh = (getattr(settings, "EUROVISION_BOOKLET_GITHUB_REPO", None) or "").strip()
            if "/" not in gh:
                return manifest
            owner, _, repo = gh.partition("/")
            branch = (
                getattr(settings, "EUROVISION_BOOKLET_GIT_BRANCH", None) or "main"
            ).strip()
            token = (getattr(settings, "EUROVISION_BOOKLET_GITHUB_TOKEN", None) or "").strip()
            try:
                manifest = _sync_github(
                    owner=owner,
                    repo=repo,
                    branch=branch,
                    dist_path=dist_path,
                    token=token or None,
                    cache_dir=cache_dir,
                    manifest=manifest,
                    timeout=timeout,
                )
            except (HTTPError, URLError, OSError, KeyError, ValueError, json.JSONDecodeError):
                logger.exception("Eurovision booklet: GitHub sync failed")
                return _read_json(manifest_path) or manifest

        _atomic_write_json(manifest_path, manifest)
        return manifest
    finally:
        if fd >= 0:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)


def meta_for_filename(manifest: dict[str, Any], name: str) -> PdfMeta | None:
    row = (manifest.get("files") or {}).get(name)
    if not row:
        return None
    sha = (row.get("commit") or "").strip()
    ds = (row.get("commit_date") or "").strip()
    if not sha or not ds:
        return None
    try:
        dt = _parse_git_datetime(ds)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_timezone.utc)
    except ValueError:
        return None
    return PdfMeta(commit=sha, commit_date=dt)


def pdf_url_for(
    settings,
    manifest: dict[str, Any],
    filename: str,
) -> str:
    from django.templatetags.static import static
    from django.urls import reverse

    cache_path = _cache_root(Path(settings.BASE_DIR)) / filename
    if cache_path.is_file():
        return reverse(
            "eurovision_booklet_pdf",
            kwargs={"filename": filename},
        )
    return static(f"microsites/eurovision_booklet/2026/{filename}")
