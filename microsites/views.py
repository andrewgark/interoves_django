import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.http import Http404, HttpResponse, FileResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

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


@require_GET
def eurovision_booklet_2026(request):
    return render(request, "microsites/eurovision_booklet_2026.html")
