import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.http import Http404, HttpResponse, FileResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from microsites.eurovision_booklet_sync import (
    BOOKLET_MINISITE_SECTIONS,
    BOOKLET_PDF_FILENAMES,
    ensure_eurovision_booklet_sync,
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


def _booklet_cached_pdfs_latest_mtime() -> datetime | None:
    base = Path(settings.BASE_DIR) / "var" / "eurovision_booklet" / "2026"
    latest: datetime | None = None
    for _sid, _title, ru, en in BOOKLET_MINISITE_SECTIONS:
        for name in (ru, en):
            p = base / name
            if p.is_file():
                d = datetime.fromtimestamp(p.stat().st_mtime, tz=dt_timezone.utc)
                if latest is None or d > latest:
                    latest = d
    return latest


def _booklet_static_pdfs_latest_mtime() -> datetime | None:
    latest: datetime | None = None
    for _sid, _title, ru, en in BOOKLET_MINISITE_SECTIONS:
        for name in (ru, en):
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


def _booklet_last_updated_display(manifest: dict) -> dict | None:
    # Prefer real content age from git (per-file last-changing commits), not
    # manifest "synced_at" (that is only when this server last ran a sync).
    dt = _booklet_latest_git_commit_datetime(manifest)
    if dt is None:
        dt = _parse_booklet_synced_at((manifest or {}).get("synced_at") or "")
    if dt is None:
        dt = _booklet_cached_pdfs_latest_mtime()
    if dt is None:
        dt = _booklet_static_pdfs_latest_mtime()
    if dt is None:
        return None
    return {"dt": dt, "iso": dt.isoformat()}


def _booklet_meta_dict(manifest: dict, filename: str) -> dict | None:
    m = meta_for_filename(manifest, filename)
    if not m:
        return None
    return {
        "commit": m.commit,
        "commit_date": m.commit_date,
        "commit_iso": m.commit_date.isoformat(),
    }


@require_GET
def eurovision_booklet_2026(request):
    sync_on = sync_configured(settings)
    manifest = {}
    if sync_on:
        manifest = ensure_eurovision_booklet_sync(settings)

    sections = []
    for sid, title, ru_name, en_name in BOOKLET_MINISITE_SECTIONS:
        sections.append(
            {
                "id": sid,
                "title": title,
                "pdf_ru_url": pdf_url_for(settings, manifest, ru_name),
                "pdf_en_url": pdf_url_for(settings, manifest, en_name),
                "meta_ru": _booklet_meta_dict(manifest, ru_name),
                "meta_en": _booklet_meta_dict(manifest, en_name),
            }
        )

    ctx = {
        "booklet_sections": sections,
        "booklet_sync_configured": sync_on,
        "booklet_branch_tip": (manifest or {}).get("branch_tip", ""),
        "booklet_synced_at": (manifest or {}).get("synced_at", ""),
        "booklet_last_updated": _booklet_last_updated_display(manifest),
        "booklet_source_url": (
            getattr(settings, "EUROVISION_BOOKLET_SOURCE_URL", "") or ""
        ).strip(),
    }
    return render(request, "microsites/eurovision_booklet_2026.html", ctx)


@require_GET
def eurovision_booklet_pdf(request, filename: str):
    if filename not in BOOKLET_PDF_FILENAMES:
        raise Http404()
    cache_path = (
        Path(settings.BASE_DIR) / "var" / "eurovision_booklet" / "2026" / filename
    )
    if cache_path.is_file():
        return FileResponse(
            cache_path.open("rb"),
            content_type="application/pdf",
        )
    absolute_path = finders.find(
        f"microsites/eurovision_booklet/2026/{filename}"
    )
    if not absolute_path:
        raise Http404()
    return FileResponse(
        open(absolute_path, "rb"),
        content_type="application/pdf",
    )
