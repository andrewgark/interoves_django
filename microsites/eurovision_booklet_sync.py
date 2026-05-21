"""
Sync Eurovision booklet PDFs and web assets (HTML bundles + shared images) from
git (local clone or GitHub API).

HTML lives under dist/html/<slug>/; shared images under assets/ at the booklet
repo root. Cached copies: BASE_DIR/var/eurovision_booklet/2026/ (PDFs, html/,
assets/). See EUROVISION_BOOKLET_* settings and microsites.views.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
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

# Web HTML bundles use the same stem as each PDF (folder dist/html/<stem>/ in the booklet repo).
BOOKLET_HTML_SLUGS: tuple[str, ...] = tuple(n[:-4] for n in BOOKLET_PDF_FILENAMES)


def _booklet_web_bundles_present(cache_dir: Path) -> bool:
    """True if every HTML bundle folder has index.html under var/.../html/<slug>/."""
    html_root = cache_dir / "html"
    if not html_root.is_dir():
        return False
    for slug in BOOKLET_HTML_SLUGS:
        if not (html_root / slug / "index.html").is_file():
            return False
    return True


def _booklet_pdf_files_present(cache_dir: Path) -> bool:
    """True if all PDFs exist in var cache (same dir as manifest)."""
    for name in BOOKLET_PDF_FILENAMES:
        if not (cache_dir / name).is_file():
            return False
    return True


def _booklet_pdf_repo_paths(dist_path: str) -> list[tuple[str, str]]:
    """(cache_basename, path_in_repo) for each PDF."""
    dist_path = dist_path.strip().strip("/")
    return [(n, f"{dist_path}/{n}") for n in BOOKLET_PDF_FILENAMES]


def _cache_web_dest_path(cache_dir: Path, dist_path_n: str, repo_path: str) -> Path | None:
    """Map booklet repo path to var/.../2026/html/... or var/.../2026/assets/..."""
    pfx_html = f"{dist_path_n}/html/"
    if repo_path.startswith(pfx_html):
        rest = repo_path[len(pfx_html) :].lstrip("/")
        if not rest:
            return None
        return cache_dir.joinpath("html", *Path(rest).parts)
    if repo_path.startswith("assets/"):
        return cache_dir.joinpath(*Path(repo_path).parts)
    return None


def _download_github_booklet_web(
    *,
    base_api: str,
    owner: str,
    repo: str,
    tip: str,
    dist_path_n: str,
    token: str | None,
    cache_dir: Path,
    timeout: float,
    paths_only: set[str] | None,
) -> None:
    """Populate var/.../html and .../assets from GitHub. paths_only=None means full tree."""
    if paths_only:
        n_ok = 0
        raw_base = f"https://raw.githubusercontent.com/{owner}/{repo}/{tip}/"
        for path in sorted(paths_only):
            dest = _cache_web_dest_path(cache_dir, dist_path_n, path)
            if dest is None:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            url = raw_base + quote(path, safe="/")
            try:
                _, content = _github_request(url, token, timeout=min(timeout, 45.0))
            except HTTPError as e:
                if e.code == 404:
                    logger.info(
                        "Eurovision booklet: web file missing on GitHub, skip: %s", path
                    )
                    continue
                raise
            dest.write_bytes(content)
            n_ok += 1
        logger.info(
            "Eurovision booklet: patched %s / %s web files from GitHub (incremental)",
            n_ok,
            len(paths_only),
        )
        return

    _, body = _github_request(f"{base_api}/commits/{tip}", token, timeout)
    tree_sha = json.loads(body.decode("utf-8"))["commit"]["tree"]["sha"]
    _, tbody = _github_request(
        f"{base_api}/git/trees/{tree_sha}?recursive=1", token, timeout
    )
    tree = json.loads(tbody.decode("utf-8"))
    if tree.get("truncated"):
        logger.warning(
            "Eurovision booklet: GitHub tree response truncated; web sync may be incomplete",
        )
    html_prefix = f"{dist_path_n}/html/"
    paths: list[str] = []
    for item in tree.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = (item.get("path") or "").strip()
        if path.startswith(html_prefix) or path.startswith("assets/"):
            paths.append(path)
    if not paths:
        logger.warning(
            "Eurovision booklet: GitHub tree has no %s/html/ or assets/ blobs at %s — "
            "commit and push the booklet web build (%s/html/<slug>/…, assets/) or set "
            "EUROVISION_BOOKLET_HTML_BASE_URL on the server.",
            dist_path_n,
            tip[:7],
            dist_path_n,
        )
        return
    shutil.rmtree(cache_dir / "html", ignore_errors=True)
    shutil.rmtree(cache_dir / "assets", ignore_errors=True)
    raw_base = f"https://raw.githubusercontent.com/{owner}/{repo}/{tip}/"
    n_ok = 0
    for path in paths:
        dest = _cache_web_dest_path(cache_dir, dist_path_n, path)
        if dest is None:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = raw_base + quote(path, safe="/")
        try:
            _, content = _github_request(url, token, timeout=min(timeout, 45.0))
        except HTTPError as e:
            if e.code == 404:
                logger.info(
                    "Eurovision booklet: web file missing on GitHub, skip: %s", path
                )
                continue
            raise
        dest.write_bytes(content)
        n_ok += 1
    logger.info(
        "Eurovision booklet: synced %s / %s web files from GitHub (full)",
        n_ok,
        len(paths),
    )


def _download_local_git_booklet_web(
    *,
    repo: Path,
    tip: str,
    dist_path: str,
    cache_dir: Path,
    timeout: float,
    paths_only: set[str] | None,
) -> None:
    """paths_only=None → replace entire html/ + assets/ from git ls-tree."""
    if paths_only:
        n_ok = 0
        for rel_repo in sorted(paths_only):
            dest = _cache_web_dest_path(cache_dir, dist_path, rel_repo)
            if dest is None:
                continue
            show = subprocess.run(
                ["git", "-C", str(repo), "show", f"{tip}:{rel_repo}"],
                capture_output=True,
                timeout=min(timeout, 120.0),
            )
            if show.returncode != 0 or not show.stdout:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(show.stdout)
            n_ok += 1
        logger.info(
            "Eurovision booklet: patched %s web files from local git (incremental)",
            n_ok,
        )
        return

    shutil.rmtree(cache_dir / "html", ignore_errors=True)
    shutil.rmtree(cache_dir / "assets", ignore_errors=True)
    n_ok = 0
    for spec in (f"{dist_path}/html", "assets"):
        ls = _git_run(
            repo,
            ["ls-tree", "-r", "--name-only", tip, "--", spec],
            timeout=timeout,
        )
        if ls.returncode != 0:
            continue
        for line in ls.stdout.splitlines():
            rel_repo = line.strip()
            if not rel_repo:
                continue
            dest = _cache_web_dest_path(cache_dir, dist_path, rel_repo)
            if dest is None:
                continue
            show = subprocess.run(
                ["git", "-C", str(repo), "show", f"{tip}:{rel_repo}"],
                capture_output=True,
                timeout=min(timeout, 120.0),
            )
            if show.returncode != 0 or not show.stdout:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(show.stdout)
            n_ok += 1
    logger.info(
        "Eurovision booklet: synced %s web files from local git (full)",
        n_ok,
    )

# Sections on the public minisite (subset of BOOKLET_PDF_FILENAMES).
BOOKLET_MINISITE_SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "final",
        "Final",
        "eurovision2026_final_ru.pdf",
        "eurovision2026_final_en.pdf",
    ),
    (
        "semi-1",
        "Semi-Final 1",
        "eurovision2026_sf1_ru.pdf",
        "eurovision2026_sf1_en.pdf",
    ),
    (
        "semi-2",
        "Semi-Final 2",
        "eurovision2026_sf2_ru.pdf",
        "eurovision2026_sf2_en.pdf",
    ),
    (
        "all-countries",
        "All countries",
        "eurovision2026_ru.pdf",
        "eurovision2026_en.pdf",
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

    dist_path_n = dist_path.strip().strip("/")
    allowed = set(BOOKLET_PDF_FILENAMES)
    web_changed_paths: set[str] = set()

    # Same commit as manifest — usually skip work, unless this instance's disk is
    # incomplete (new EB instance, failed prior web sync, etc.).
    if old_tip == tip:
        pdf_ok = _booklet_pdf_files_present(cache_dir)
        web_ok = _booklet_web_bundles_present(cache_dir)
        if pdf_ok and web_ok:
            manifest.setdefault("branch_tip", tip)
            return manifest
        logger.info(
            "Eurovision booklet: GitHub tip %s equals manifest but local cache incomplete "
            "(pdf=%s web=%s) — refilling",
            tip[:7],
            pdf_ok,
            web_ok,
        )
        changed = {n for n in allowed if not (cache_dir / n).is_file()}
        web_dirty = not web_ok
        web_changed_paths = set()
    elif not old_tip:
        changed = set(allowed)
        web_dirty = True
    else:
        web_dirty = False
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
                changed = set(allowed)
                web_dirty = True
            else:
                names = set()
                for f in cmp.get("files") or []:
                    fn = f.get("filename") or ""
                    if fn.lower().endswith(".pdf"):
                        names.add(Path(fn).name)
                    if fn.startswith(f"{dist_path_n}/html/") or fn.startswith("assets/"):
                        web_dirty = True
                        web_changed_paths.add(fn)
                changed = names & allowed
        except HTTPError as e:
            if e.code == 404:
                logger.warning(
                    "Eurovision booklet: compare %s...%s not found; full refresh",
                    old_tip[:7],
                    tip[:7],
                )
                changed = set(allowed)
                web_dirty = True
            else:
                raise

    if old_tip and not changed and not web_dirty:
        manifest["branch_tip"] = tip
        manifest["backend"] = "github"
        manifest["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return manifest

    files_meta: dict[str, Any] = dict(manifest.get("files") or {})

    for name, rel in _booklet_pdf_repo_paths(dist_path_n):
        if name not in changed:
            continue
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
            _, file_bytes = _github_request(raw_url, token, timeout)
        except HTTPError as e:
            if e.code == 404:
                logger.info("Eurovision booklet: raw %s missing — skipping", rel)
                continue
            raise
        out = cache_dir / name
        out.write_bytes(file_bytes)
        files_meta[name] = {"commit": sha, "commit_date": date_s}
        logger.info("Eurovision booklet: updated %s from GitHub", name)

    if web_dirty:
        if not old_tip or not web_changed_paths or changed == allowed:
            web_paths_only: set[str] | None = None
        else:
            web_paths_only = set(web_changed_paths)
        try:
            _download_github_booklet_web(
                base_api=base_api,
                owner=owner,
                repo=repo,
                tip=tip,
                dist_path_n=dist_path_n,
                token=token,
                cache_dir=cache_dir,
                timeout=timeout,
                paths_only=web_paths_only,
            )
        except Exception:
            logger.exception("Eurovision booklet: GitHub web bundle sync failed")

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

    allowed = set(BOOKLET_PDF_FILENAMES)
    web_changed_paths: set[str] = set()

    if old_tip == tip:
        pdf_ok = _booklet_pdf_files_present(cache_dir)
        web_ok = _booklet_web_bundles_present(cache_dir)
        if pdf_ok and web_ok:
            manifest.setdefault("branch_tip", tip)
            return manifest
        logger.info(
            "Eurovision booklet: local git tip %s equals manifest but cache incomplete "
            "(pdf=%s web=%s) — refilling",
            tip[:7],
            pdf_ok,
            web_ok,
        )
        changed = {n for n in allowed if not (cache_dir / n).is_file()}
        web_dirty = not web_ok
        web_changed_paths = set()
    elif not old_tip:
        changed = set(allowed)
        web_dirty = True
    else:
        web_dirty = False
        diff = _git_run(
            repo,
            ["diff", "--name-only", old_tip, tip, "--", dist_path, "assets"],
            timeout=timeout,
        )
        if diff.returncode != 0:
            logger.warning(
                "Eurovision booklet: git diff %s..%s failed; refreshing all PDFs",
                old_tip[:7],
                tip[:7],
            )
            changed = set(allowed)
            web_dirty = True
        else:
            changed = set()
            for line in diff.stdout.splitlines():
                line = line.strip()
                if line.lower().endswith(".pdf"):
                    changed.add(Path(line).name)
                if line.startswith(f"{dist_path}/html/") or line.startswith("assets/"):
                    web_dirty = True
                    web_changed_paths.add(line)
            changed &= allowed

    if old_tip and not changed and not web_dirty:
        manifest["branch_tip"] = tip
        manifest["backend"] = "git_local"
        manifest["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return manifest

    files_meta: dict[str, Any] = dict(manifest.get("files") or {})

    for name, rel in _booklet_pdf_repo_paths(dist_path):
        if name not in changed:
            continue
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

    if web_dirty:
        if not old_tip or not web_changed_paths or changed == allowed:
            web_paths_only = None
        else:
            web_paths_only = set(web_changed_paths)
        try:
            _download_local_git_booklet_web(
                repo=repo,
                tip=tip,
                dist_path=dist_path,
                cache_dir=cache_dir,
                timeout=timeout,
                paths_only=web_paths_only,
            )
        except Exception:
            logger.exception("Eurovision booklet: local git web bundle sync failed")

    manifest["branch_tip"] = tip
    manifest["backend"] = "git_local"
    manifest["files"] = files_meta
    manifest["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return manifest


def sync_configured(settings) -> bool:
    gh = (getattr(settings, "EUROVISION_BOOKLET_GITHUB_REPO", None) or "").strip()
    loc = (getattr(settings, "EUROVISION_BOOKLET_REPO_PATH", None) or "").strip()
    return bool(gh or loc)


def should_skip_throttle(settings, cache_dir: Path) -> bool:
    """Skip remote sync if interval elapsed — unless this instance lacks local cache.

    Cache key is global (Redis); EB instances have separate disks. Another instance
    may have synced recently and set the cooldown while this host still has an
    empty var/. In that case we must not skip.
    """
    if not _booklet_web_bundles_present(cache_dir):
        return False
    if not _booklet_pdf_files_present(cache_dir):
        return False
    interval = int(getattr(settings, "EUROVISION_BOOKLET_SYNC_MIN_INTERVAL_SEC", 120))
    if interval <= 0:
        return False
    try:
        from django.core.cache import cache
    except Exception:
        return False
    return not cache.add("eurovision_booklet:sync_cooldown", 1, timeout=interval)


def ensure_eurovision_booklet_sync(settings) -> dict[str, Any]:
    """
    Update cached PDFs and web bundles (dist/html + assets) when the remote
    branch tip changed.

    PDFs: refresh only files that differ between the previous tip and the
    current tip. Web: full tree on first sync or after a broad refresh;
    otherwise patch only paths touched in the compare range.

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
    if should_skip_throttle(settings, cache_dir):
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
    _settings,
    _manifest: dict[str, Any],
    filename: str,
) -> str:
    """URL for a booklet PDF. Always uses the named route so the view can serve
    from var cache or staticfiles finders (avoids broken /static/... when files
    are not collected under STATIC_URL).
    """
    from django.urls import reverse

    return reverse("eurovision_booklet_pdf", kwargs={"filename": filename})


def html_url_for(
    _settings,
    _manifest: dict[str, Any],
    slug: str,
) -> str:
    """Canonical URL for the web bundle (folder dist/html/<slug>/, index at trailing /)."""
    from django.urls import reverse

    return reverse("eurovision_booklet_html", kwargs={"slug": slug})
