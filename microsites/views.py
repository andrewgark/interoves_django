import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
from datetime import datetime, timezone as dt_timezone
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.staticfiles import finders
from django.http import Http404, HttpResponse, HttpResponseRedirect, FileResponse
from django.shortcuts import render
from django.utils.http import http_date
from django.views.decorators.http import require_GET

from microsites.eurovision_booklet_sync import (
    BOOKLET_HTML_SLUGS,
    BOOKLET_MINISITE_SECTIONS,
    BOOKLET_PDF_FILENAMES,
    booklet_auto_sync_enabled,
    booklet_cache_control,
    booklet_pinned_ref,
    ensure_eurovision_booklet_sync,
    html_url_for,
    meta_for_filename,
    pdf_url_for,
    sync_configured,
)

logger = logging.getLogger(__name__)

_SAFE_WEB_NAME = re.compile(r"^[a-zA-Z0-9][-a-zA-Z0-9_.]*$")


def expanduser_maybe(p: str) -> str:
    return str(Path(p).expanduser())


def _resolve_nutrimatic_index_path(root: Path, ix: str) -> Path | None:
    bucket = (getattr(settings, "NUTRIMATIC_INDEX_S3_BUCKET", None) or "").strip()
    s3_key = (getattr(settings, "NUTRIMATIC_INDEX_S3_KEY", None) or "").strip()
    if bucket and s3_key:
        from microsites.nutrimatic_s3_index import ensure_nutrimatic_index_from_s3

        try:
            cached = ensure_nutrimatic_index_from_s3()
            if cached is not None and cached.is_file():
                return cached
        except Exception:
            logger.exception("Nutrimatic index: S3 download failed; trying local paths")

    if ix:
        p = Path(expanduser_maybe(ix))
        if p.is_file():
            return p
        p = root / ix
        if p.is_file():
            return p
        return None

    index = root / "wiki-merged.index"
    if index.is_file():
        return index
    found = sorted(root.glob("*.index"))
    if found:
        return found[0]
    return None


def _nutrimatic_paths():
    root_raw = getattr(settings, "NUTRIMATIC_ROOT", "") or ""
    root = root_raw.strip()
    if not root:
        return None
    root = Path(root)
    fe = (getattr(settings, "NUTRIMATIC_FIND_EXPR", "") or "").strip()
    ix = (getattr(settings, "NUTRIMATIC_INDEX", "") or "").strip()
    cg = (getattr(settings, "NUTRIMATIC_CGI_SCRIPT", "") or "").strip()
    find_expr = Path(expanduser_maybe(fe)) if fe else (root / "build" / "find-expr")
    index = _resolve_nutrimatic_index_path(root, ix)
    cgi = Path(expanduser_maybe(cg)) if cg else (root / "cgi_scripts" / "cgi-search.py")
    if index is None:
        return None
    return root, find_expr, index, cgi


@require_GET
def nutrimatic_search(request):
    paths = _nutrimatic_paths()
    if not paths:
        return HttpResponse(
            "<!DOCTYPE html><html lang=\"ru\"><head><meta charset=\"utf-8\"><title>Nutrimatic</title></head>"
            "<body><h1>Nutrimatic</h1>"
            "<p>Поиск не настроен на этом сервере (задайте <code>NUTRIMATIC_ROOT</code>).</p>"
            "</body></html>",
            status=503,
            content_type="text/html; charset=utf-8",
        )
    _, find_expr, index, cgi = paths
    if not find_expr.is_file() or not index.is_file() or not cgi.is_file():
        return HttpResponse(
            "<!DOCTYPE html><html lang=\"ru\"><head><meta charset=\"utf-8\"><title>Nutrimatic</title></head>"
            "<body><h1>Nutrimatic</h1>"
            "<p>Отсутствуют файлы индекса или <code>find-expr</code> (проверьте путь и деплой бандла).</p>"
            "</body></html>",
            status=503,
            content_type="text/html; charset=utf-8",
        )

    env = {**os.environ}
    env["REQUEST_METHOD"] = "GET"
    env["QUERY_STRING"] = request.META.get("QUERY_STRING", "")
    env["NUTRIMATIC_FIND_EXPR"] = str(find_expr)
    env["NUTRIMATIC_INDEX"] = str(index)
    try:
        proc = subprocess.run(
            [sys.executable, str(cgi)],
            env=env,
            cwd=str(paths[0]),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return HttpResponse(
            "Search timed out",
            status=504,
            content_type="text/plain; charset=utf-8",
        )

    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        msg = err or f"nutrimatic exited with code {proc.returncode}"
        return HttpResponse(msg, status=500, content_type="text/plain; charset=utf-8")

    body = _strip_cgi_headers(proc.stdout)
    body = body.replace(b'href="/favicon.ico"', b'href="/nutrimatic-ru/favicon.ico"')
    return HttpResponse(body, content_type="text/html; charset=utf-8")


def _strip_cgi_headers(raw: bytes) -> bytes:
    if not raw.startswith(b"Content-type:") and not raw.startswith(b"Content-Type:"):
        return raw
    idx = raw.find(b"\n\n")
    if idx != -1:
        return raw[idx + 2 :]
    idx = raw.find(b"\r\n\r\n")
    if idx != -1:
        return raw[idx + 4 :]
    return raw


@require_GET
def nutrimatic_web_file(request, rel_path):
    if not _SAFE_WEB_NAME.match(rel_path):
        raise Http404()
    rel = f"microsites/nutrimatic-ru/web_static/{rel_path}"
    absolute_path = finders.find(rel)
    if not absolute_path:
        raise Http404()
    return FileResponse(open(absolute_path, "rb"))


def _read_booklet_manifest_json() -> dict:
    """Manifest on disk when in-memory sync result is empty (throttle, errors)."""
    p = Path(settings.BASE_DIR) / "var" / "eurovision_booklet" / "2026" / "manifest.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _parse_booklet_synced_at(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(s)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def _booklet_cached_artifacts_latest_mtime() -> datetime | None:
    """Latest mtime among cached PDFs, all HTML bundle files, and shared assets/."""
    base = Path(settings.BASE_DIR) / "var" / "eurovision_booklet" / "2026"
    latest: datetime | None = None
    for name in BOOKLET_PDF_FILENAMES:
        p = base / name
        if p.is_file():
            d = datetime.fromtimestamp(p.stat().st_mtime, tz=dt_timezone.utc)
            if latest is None or d > latest:
                latest = d
    for slug in BOOKLET_HTML_SLUGS:
        root = base / "html" / slug
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("*"):
                if p.is_file():
                    d = datetime.fromtimestamp(p.stat().st_mtime, tz=dt_timezone.utc)
                    if latest is None or d > latest:
                        latest = d
        except OSError:
            continue
    assets = base / "assets"
    if assets.is_dir():
        try:
            for p in assets.rglob("*"):
                if p.is_file():
                    d = datetime.fromtimestamp(p.stat().st_mtime, tz=dt_timezone.utc)
                    if latest is None or d > latest:
                        latest = d
        except OSError:
            pass
    return latest


def _booklet_static_artifacts_latest_mtime() -> datetime | None:
    latest: datetime | None = None
    for name in BOOKLET_PDF_FILENAMES:
        absolute_path = finders.find(
            f"microsites/eurovision_booklet/2026/{name}"
        )
        if absolute_path:
            d = datetime.fromtimestamp(
                os.path.getmtime(absolute_path),
                tz=dt_timezone.utc,
            )
            if latest is None or d > latest:
                latest = d
    for slug in BOOKLET_HTML_SLUGS:
        rel = f"microsites/eurovision_booklet/2026/html/{slug}/index.html"
        absolute_path = finders.find(rel)
        if absolute_path:
            d = datetime.fromtimestamp(
                os.path.getmtime(absolute_path),
                tz=dt_timezone.utc,
            )
            if latest is None or d > latest:
                latest = d
    return latest


def _booklet_latest_git_commit_datetime(manifest: dict) -> datetime | None:
    """Newest PDF commit timestamp from the sync manifest (Git/GitHub), if any."""
    latest: datetime | None = None
    for name in BOOKLET_PDF_FILENAMES:
        m = meta_for_filename(manifest, name)
        if m is None:
            continue
        if latest is None or m.commit_date > latest:
            latest = m.commit_date
    return latest


def _touch_eurovision_booklet_sync() -> None:
    """Pull PDFs + web bundles from GitHub/git into var/ when configured.

    Must run on HTML/asset/PDF views too — not only the landing page — or direct
    links to /eurovision_booklet/2026/html/… never populate the cache.

    Frozen mode (pin + AUTO_SYNC=False): no tip polling; at most a one-time fill
    of an empty var/ from EUROVISION_BOOKLET_PINNED_REF.
    """
    if not sync_configured(settings):
        return
    # Hard off without a pin: never hit the network from request paths.
    if not booklet_auto_sync_enabled(settings) and not booklet_pinned_ref(settings):
        return
    ensure_eurovision_booklet_sync(settings)


def _booklet_last_updated_display(manifest: dict) -> dict | None:
    # Git dates in manifest are only recorded for PDFs; HTML-only pushes do not
    # move that clock. Combine with real file mtimes under var/ so "Last updated"
    # reflects synced web bundles and inner pages, not only PDF commit metadata.
    m = manifest or {}
    dt_git = _booklet_latest_git_commit_datetime(m)
    dt_disk = _booklet_cached_artifacts_latest_mtime()
    dt = None
    for cand in (dt_git, dt_disk):
        if cand is None:
            continue
        if dt is None or cand > dt:
            dt = cand
    if dt is None:
        dt = _parse_booklet_synced_at((m.get("synced_at") or ""))
    if dt is None:
        dt = _booklet_static_artifacts_latest_mtime()
    if dt is None:
        mp = Path(settings.BASE_DIR) / "var" / "eurovision_booklet" / "2026" / "manifest.json"
        if mp.is_file():
            dt = datetime.fromtimestamp(mp.stat().st_mtime, tz=dt_timezone.utc)
    if dt is None:
        return None
    return {"dt": dt, "iso": dt.isoformat()}


@require_GET
def eurovision_booklet_2026(request):
    sync_on = sync_configured(settings)
    manifest = {}
    if sync_on and (
        booklet_auto_sync_enabled(settings) or booklet_pinned_ref(settings)
    ):
        manifest = ensure_eurovision_booklet_sync(settings)
    disk_manifest = _read_booklet_manifest_json()
    manifest_display = {**(disk_manifest or {}), **manifest}

    sections = []
    for sid, title, ru_name, en_name in BOOKLET_MINISITE_SECTIONS:
        sections.append(
            {
                "id": sid,
                "title": title,
                "pdf_ru_url": pdf_url_for(settings, manifest_display, ru_name),
                "pdf_en_url": pdf_url_for(settings, manifest_display, en_name),
                "html_ru_url": html_url_for(settings, manifest_display, ru_name[:-4]),
                "html_en_url": html_url_for(settings, manifest_display, en_name[:-4]),
            }
        )

    ctx = {
        "booklet_sections": sections,
        "booklet_sync_configured": sync_on,
        "booklet_branch_tip": (manifest_display or {}).get("branch_tip", ""),
        "booklet_synced_at": (manifest_display or {}).get("synced_at", ""),
        "booklet_last_updated": _booklet_last_updated_display(manifest_display),
        "booklet_source_url": (
            getattr(settings, "EUROVISION_BOOKLET_SOURCE_URL", "") or ""
        ).strip(),
    }
    return render(request, "microsites/eurovision_booklet_2026.html", ctx)


@require_GET
def eurovision_booklet_pdf(request, filename: str):
    _touch_eurovision_booklet_sync()
    if filename not in BOOKLET_PDF_FILENAMES:
        raise Http404()
    cache_path = (
        Path(settings.BASE_DIR) / "var" / "eurovision_booklet" / "2026" / filename
    )
    cc = booklet_cache_control(settings)
    if cache_path.is_file():
        resp = FileResponse(
            cache_path.open("rb"),
            content_type="application/pdf",
        )
        stat = cache_path.stat()
        resp["Cache-Control"] = cc
        resp["Last-Modified"] = http_date(stat.st_mtime)
        resp["ETag"] = f'W/"{stat.st_size:x}-{int(stat.st_mtime):x}"'
        return resp
    absolute_path = finders.find(
        f"microsites/eurovision_booklet/2026/{filename}"
    )
    if not absolute_path:
        dist = _booklet_dist_prefix(settings)
        fetched = _fetch_github_raw_booklet(settings, f"{dist}/{filename}")
        if fetched is not None:
            body, _ctype = fetched
            resp = FileResponse(
                BytesIO(body),
                content_type="application/pdf",
                as_attachment=False,
                filename=filename,
            )
            resp["Cache-Control"] = cc
            return resp
        raise Http404()
    resp = FileResponse(open(absolute_path, "rb"), content_type="application/pdf")
    try:
        st = os.stat(absolute_path)
        resp["Cache-Control"] = cc
        resp["Last-Modified"] = http_date(st.st_mtime)
        resp["ETag"] = f'W/"{st.st_size:x}-{int(st.st_mtime):x}"'
    except OSError:
        resp["Cache-Control"] = cc
    return resp


def _booklet_local_html_root(settings) -> Path | None:
    raw = (getattr(settings, "EUROVISION_BOOKLET_LOCAL_HTML_DIR", None) or "").strip()
    if not raw:
        return None
    p = Path(expanduser_maybe(raw))
    return p if p.is_dir() else None


def _booklet_dist_prefix(settings) -> str:
    return (getattr(settings, "EUROVISION_BOOKLET_DIST_PATH", None) or "dist").strip().strip(
        "/"
    )


def _booklet_github_raw_url(settings, repo_relpath: str) -> str | None:
    """https://raw.githubusercontent.com/owner/repo/<ref>/path — only when repo is configured.

    Uses EUROVISION_BOOKLET_PINNED_REF when set so fallbacks do not float with main.
    """
    gh = (getattr(settings, "EUROVISION_BOOKLET_GITHUB_REPO", None) or "").strip()
    if "/" not in gh:
        return None
    owner, _, repo = gh.partition("/")
    if not owner or not repo:
        return None
    ref = booklet_pinned_ref(settings) or (
        getattr(settings, "EUROVISION_BOOKLET_GIT_BRANCH", None) or "main"
    ).strip()
    rel = repo_relpath.strip().strip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    parts = [quote(seg, safe="") for seg in rel.split("/")]
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{'/'.join(parts)}"


def _fetch_github_raw_booklet(
    settings, repo_relpath: str
) -> tuple[bytes, str] | None:
    """GET repo file from raw.githubusercontent.com (same-origin proxy for HTML/CSS)."""
    url = _booklet_github_raw_url(settings, repo_relpath)
    if not url:
        return None
    timeout = float(getattr(settings, "EUROVISION_BOOKLET_HTTP_TIMEOUT", 60.0))
    timeout = max(1.0, min(timeout, 120.0))
    req = Request(url, headers={"User-Agent": "interoves-django (eurovision-booklet)"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", resp.getcode())
            if code != 200:
                return None
            body = resp.read()
            ctype_header = resp.headers.get("Content-Type") or ""
            header_base = ctype_header.split(";")[0].strip().lower()
            basename = Path(repo_relpath).name
            suf = Path(repo_relpath).suffix.lower()
            # GitHub raw uses text/plain for .html/.css etc.; browsers must see real types.
            if suf in (".html", ".htm"):
                ctype = "text/html; charset=utf-8"
            elif suf == ".css":
                ctype = "text/css; charset=utf-8"
            elif suf in (".js", ".mjs"):
                ctype = "text/javascript; charset=utf-8"
            elif suf == ".svg":
                ctype = "image/svg+xml; charset=utf-8"
            elif suf == ".pdf":
                ctype = "application/pdf"
            else:
                ctype = ctype_header.split(";")[0].strip()
                if not ctype or ctype.lower() == "application/octet-stream":
                    guessed, _enc = mimetypes.guess_type(basename)
                    if guessed:
                        ctype = guessed
                if header_base == "text/plain" and ctype.lower() in (
                    "text/plain",
                    "application/octet-stream",
                ):
                    guessed, _enc = mimetypes.guess_type(basename)
                    if guessed and guessed != "text/plain":
                        ctype = guessed
                if ctype == "text/html":
                    ctype = "text/html; charset=utf-8"
                elif ctype == "text/css":
                    ctype = "text/css; charset=utf-8"
                elif ctype in ("application/javascript", "text/javascript"):
                    ctype = "text/javascript; charset=utf-8"
            return body, ctype or "application/octet-stream"
    except HTTPError as e:
        if e.code != 404:
            logger.info("Eurovision booklet: raw HTTP %s %s", e.code, url)
        return None
    except (URLError, OSError, TimeoutError, ValueError) as e:
        logger.info("Eurovision booklet: raw fetch failed %s: %s", url, e)
        return None


def _booklet_local_assets_root(settings) -> Path | None:
    """Booklet repo `assets/` (flags, artists, …). HTML uses ../../../assets/... → /eurovision_booklet/assets/..."""
    raw = (getattr(settings, "EUROVISION_BOOKLET_LOCAL_ASSETS_DIR", None) or "").strip()
    if raw:
        p = Path(expanduser_maybe(raw))
        if p.is_dir():
            return p
    html_root = _booklet_local_html_root(settings)
    if html_root is None:
        return None
    # …/dist/html → …/assets
    cand = html_root.parent.parent / "assets"
    return cand if cand.is_dir() else None


def _safe_booklet_bundle_file(root: Path, relpath: str) -> Path | None:
    if ".." in Path(relpath).parts:
        return None
    try:
        cand = (root / relpath).resolve()
        root = root.resolve()
    except OSError:
        return None
    try:
        cand.relative_to(root)
    except ValueError:
        return None
    return cand if cand.is_file() else None


def _resolve_booklet_html_bundle_file(settings, slug: str, relpath: str) -> Path | None:
    rel = relpath.strip().strip("/") or "index.html"
    cache_root = Path(settings.BASE_DIR) / "var" / "eurovision_booklet" / "2026" / "html" / slug
    hit = _safe_booklet_bundle_file(cache_root, rel)
    if hit is not None:
        return hit
    lr = _booklet_local_html_root(settings)
    if lr is not None:
        hit = _safe_booklet_bundle_file(lr / slug, rel)
        if hit is not None:
            return hit
    static_rel = f"microsites/eurovision_booklet/2026/html/{slug}/{rel}"
    found = finders.find(static_rel)
    if found:
        return Path(found)
    return None


def _file_response_booklet_asset(path: Path) -> FileResponse:
    ctype, _enc = mimetypes.guess_type(path.name)
    if not ctype:
        ctype = "application/octet-stream"
    if ctype == "text/html":
        ctype = "text/html; charset=utf-8"
    elif ctype == "text/css":
        ctype = "text/css; charset=utf-8"
    elif ctype == "application/javascript":
        ctype = "text/javascript; charset=utf-8"
    resp = FileResponse(path.open("rb"), content_type=ctype)
    stat = path.stat()
    resp["Cache-Control"] = booklet_cache_control(settings)
    resp["Last-Modified"] = http_date(stat.st_mtime)
    resp["ETag"] = f'W/"{stat.st_size:x}-{int(stat.st_mtime):x}"'
    return resp


@require_GET
def eurovision_booklet_html_bundle(request, slug: str, relpath: str = "index.html"):
    _touch_eurovision_booklet_sync()
    if slug not in BOOKLET_HTML_SLUGS:
        raise Http404()
    rel = (relpath or "").strip().strip("/") or "index.html"
    resolved = _resolve_booklet_html_bundle_file(settings, slug, rel)
    if resolved is not None:
        return _file_response_booklet_asset(resolved)
    base = (getattr(settings, "EUROVISION_BOOKLET_HTML_BASE_URL", None) or "").strip().rstrip("/")
    if base:
        return HttpResponseRedirect(f"{base}/{slug}/{rel}")
    dist = _booklet_dist_prefix(settings)
    gh_rel = f"{dist}/html/{slug}/{rel}"
    fetched = _fetch_github_raw_booklet(settings, gh_rel)
    if fetched is not None:
        body, ctype = fetched
        resp = HttpResponse(body, content_type=ctype)
        resp["Cache-Control"] = booklet_cache_control(settings)
        return resp
    probe = (
        Path(settings.BASE_DIR)
        / "var"
        / "eurovision_booklet"
        / "2026"
        / "html"
        / slug
        / "index.html"
    )
    logger.warning(
        "Eurovision booklet: missing HTML bundle slug=%s rel=%s (expected %s). "
        "Publish dist/html/ to EUROVISION_BOOKLET_GITHUB_REPO or set EUROVISION_BOOKLET_HTML_BASE_URL.",
        slug,
        rel,
        probe,
    )
    raise Http404()


def _resolve_booklet_shared_asset_file(settings, relpath: str) -> Path | None:
    rel = relpath.strip().strip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    var_root = Path(settings.BASE_DIR) / "var" / "eurovision_booklet" / "2026" / "assets"
    hit = _safe_booklet_bundle_file(var_root, rel)
    if hit is not None:
        return hit
    static_rel = f"microsites/eurovision_booklet/2026/assets/{rel}"
    found = finders.find(static_rel)
    if found:
        return Path(found)
    ar = _booklet_local_assets_root(settings)
    if ar is not None:
        return _safe_booklet_bundle_file(ar, rel)
    return None


@require_GET
def eurovision_booklet_shared_assets(request, relpath: str):
    """Serve booklet `assets/` (flags, photos) for relative URLs ../../../assets/… in HTML."""
    _touch_eurovision_booklet_sync()
    resolved = _resolve_booklet_shared_asset_file(settings, relpath)
    if resolved is not None:
        return _file_response_booklet_asset(resolved)
    rel = relpath.strip().strip("/")
    if rel and ".." not in Path(rel).parts:
        raw_url = _booklet_github_raw_url(settings, f"assets/{rel}")
        if raw_url:
            return HttpResponseRedirect(raw_url)
    raise Http404()
