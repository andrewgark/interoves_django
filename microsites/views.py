import json
import asyncio
import logging
import mimetypes
import os
import re
import signal
import sys
from datetime import datetime, timezone as dt_timezone
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.staticfiles import finders
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect, FileResponse
from django.shortcuts import render
from django.utils.http import http_date
from django.views.decorators.http import require_GET

from microsites.eurovision_booklet_sync import (
    BOOKLET_HTML_SLUGS,
    BOOKLET_MINISITE_SECTIONS,
    BOOKLET_PDF_FILENAMES,
    booklet_cache_control,
    booklet_pinned_ref,
    html_url_for,
    meta_for_filename,
    pdf_url_for,
    sync_configured,
)

# Injected into HTML booklet pages (lazy load overlay). Marker used by tests.
_BOOKLET_HTML_LOADING_MARKER = "data-booklet-loading-overlay"

logger = logging.getLogger(__name__)

_SAFE_WEB_NAME = re.compile(r"^[a-zA-Z0-9][-a-zA-Z0-9_.]*$")
_NUTRIMATIC_SEARCH_GATE = None
_NUTRIMATIC_GATE_LOCK = None
_NUTRIMATIC_PATHS_TASK = None
_NUTRIMATIC_PATHS_VALUE = None
_NUTRIMATIC_ACTIVE = 0
_NUTRIMATIC_QUEUED = 0
_NUTRIMATIC_QUEUE_LIMIT = 1
_NUTRIMATIC_QUEUE_WAIT_SECONDS = 1.0
_NUTRIMATIC_DEADLINE_SECONDS = 45.0
_NUTRIMATIC_KILL_GRACE_SECONDS = 1.0
_NUTRIMATIC_MAX_OUTPUT_BYTES = 8 * 1024 * 1024


def _nutrimatic_async_gate():
    """Create loop-local gate lazily (Daphne serves this app on one loop)."""
    global _NUTRIMATIC_SEARCH_GATE, _NUTRIMATIC_GATE_LOCK
    loop = asyncio.get_running_loop()
    if getattr(_NUTRIMATIC_SEARCH_GATE, "_loop", loop) not in (None, loop):
        _NUTRIMATIC_SEARCH_GATE = None
        _NUTRIMATIC_GATE_LOCK = None
    if _NUTRIMATIC_SEARCH_GATE is None:
        _NUTRIMATIC_SEARCH_GATE = asyncio.Semaphore(1)
        _NUTRIMATIC_GATE_LOCK = asyncio.Lock()
    return _NUTRIMATIC_SEARCH_GATE, _NUTRIMATIC_GATE_LOCK


async def _nutrimatic_paths_async():
    """Resolve paths off-loop and share one in-flight S3/cache initialization."""
    global _NUTRIMATIC_PATHS_TASK, _NUTRIMATIC_PATHS_VALUE
    if _NUTRIMATIC_PATHS_VALUE is not None:
        return _NUTRIMATIC_PATHS_VALUE
    if _NUTRIMATIC_PATHS_TASK is None or _NUTRIMATIC_PATHS_TASK.get_loop() is not asyncio.get_running_loop():
        _NUTRIMATIC_PATHS_TASK = asyncio.create_task(asyncio.to_thread(_nutrimatic_paths))
    try:
        value = await asyncio.shield(_NUTRIMATIC_PATHS_TASK)
    except BaseException:
        if _NUTRIMATIC_PATHS_TASK.done():
            _NUTRIMATIC_PATHS_TASK = None
        raise
    _NUTRIMATIC_PATHS_VALUE = value
    _NUTRIMATIC_PATHS_TASK = None
    return value


async def _nutrimatic_acquire():
    global _NUTRIMATIC_QUEUED, _NUTRIMATIC_ACTIVE
    gate, lock = _nutrimatic_async_gate()
    async with lock:
        if not gate.locked():
            await gate.acquire()
            _NUTRIMATIC_ACTIVE += 1
            return True
        if _NUTRIMATIC_QUEUED >= _NUTRIMATIC_QUEUE_LIMIT:
            return False
        _NUTRIMATIC_QUEUED += 1
    permit_acquired = False
    try:
        await asyncio.wait_for(gate.acquire(), timeout=_NUTRIMATIC_QUEUE_WAIT_SECONDS)
        permit_acquired = True
        async with lock:
            _NUTRIMATIC_ACTIVE += 1
            permit_acquired = False
        return True
    except asyncio.TimeoutError:
        return False
    except BaseException:
        if permit_acquired:
            gate.release()
        raise
    finally:
        async with lock:
            _NUTRIMATIC_QUEUED -= 1


async def _nutrimatic_release():
    global _NUTRIMATIC_ACTIVE
    gate, lock = _nutrimatic_async_gate()
    async with lock:
        _NUTRIMATIC_ACTIVE -= 1
        gate.release()


async def _read_bounded(stream, limit):
    chunks = []
    size = 0
    while True:
        chunk = await stream.read(min(65536, limit + 1 - size))
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > limit:
            raise ValueError("Nutrimatic output exceeded configured limit")
        chunks.append(chunk)


async def _terminate_nutrimatic_group(proc, *tasks):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = asyncio.get_running_loop().time() + _NUTRIMATIC_KILL_GRACE_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(min(0.05, max(0.0, deadline - asyncio.get_running_loop().time())))
    else:
        # The CGI may exit before a grandchild which ignores SIGTERM. Check
        # the process group itself, not just the asyncio child/pipe futures.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await proc.wait()
    # Closing the leader does not close pipes retained by escaped descendants.
    for task in tasks:
        if task is not None and not task.done():
            task.cancel()
    transport = getattr(proc, "_transport", None)
    if transport is not None:
        transport.close()


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
    resolver = getattr(settings, "resolve_nutrimatic_root", None)
    if callable(resolver):
        root_raw = resolver() or ""
    else:
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


def _nutrimatic_query_options_valid(request) -> bool:
    if "q" not in request.GET:
        return True
    if len(request.GET.getlist("q")) > 1:
        return False
    bounds = {
        "start": (0, 2_147_483_647),
        "num": (1, 10_000),
        "comp": (1, 2_147_483_647),
    }
    for key, (minimum, maximum) in bounds.items():
        values = request.GET.getlist(key)
        if not values:
            continue
        if len(values) != 1 or len(values[0]) > 10:
            return False
        try:
            value = int(values[0])
        except (TypeError, ValueError):
            return False
        if not minimum <= value <= maximum:
            return False
    return True


async def nutrimatic_search(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    if not _nutrimatic_query_options_valid(request):
        return HttpResponse(
            "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Nutrimatic — некорректный запрос</title></head><body>"
            "<main><h1>Nutrimatic</h1>"
            "<p>Параметры запроса некорректны. Проверьте их и повторите поиск.</p>"
            "<p><a href=\"/nutrimatic-ru/\">На главную</a></p>"
            "</main></body></html>",
            status=400,
            content_type="text/html; charset=utf-8",
        )
    started = asyncio.get_running_loop().time()
    if not await _nutrimatic_acquire():
        logger.info("nutrimatic rejected active=%d queued=%d", _NUTRIMATIC_ACTIVE, _NUTRIMATIC_QUEUED)
        retry_query = html.escape(request.GET.urlencode(), quote=True)
        retry_href = f"?{retry_query}" if retry_query else "/nutrimatic-ru/"
        return HttpResponse(
            "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Nutrimatic — поиск занят</title></head><body>"
            "<main><h1>Nutrimatic</h1>"
            "<p role=\"status\"><strong>Сейчас выполняется другой поиск.</strong></p>"
            "<p>Ваш запрос пока не запущен. Подождите несколько секунд и повторите.</p>"
            f"<p><a href=\"{retry_href}\">Повторить запрос</a></p>"
            "</main></body></html>",
            status=429,
            headers={"Retry-After": "5"},
            content_type="text/html; charset=utf-8",
        )
    try:
        paths = await _nutrimatic_paths_async()
    except BaseException:
        await _nutrimatic_release()
        raise
    if not paths:
        await _nutrimatic_release()
        return HttpResponse(
            "<!DOCTYPE html><html lang=\"ru\"><head><meta charset=\"utf-8\"><title>Nutrimatic</title></head>"
            "<body><h1>Nutrimatic</h1>"
            "<p>Поиск не настроен на этом сервере: нет <code>find-expr</code> или индекса "
            "(проверьте бандл и <code>NUTRIMATIC_INDEX_S3_BUCKET</code> / <code>NUTRIMATIC_ROOT</code>).</p>"
            "</body></html>",
            status=503,
            content_type="text/html; charset=utf-8",
        )
    _, find_expr, index, cgi = paths
    if not find_expr.is_file() or not index.is_file() or not cgi.is_file():
        await _nutrimatic_release()
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
    proc = None
    stdout_task = stderr_task = wait_task = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(cgi),
            env=env, cwd=str(paths[0]),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        logger.info("nutrimatic start pid=%d pgid=%d active=%d queued=%d", proc.pid, proc.pid, _NUTRIMATIC_ACTIVE, _NUTRIMATIC_QUEUED)
        stdout_task = asyncio.create_task(_read_bounded(proc.stdout, _NUTRIMATIC_MAX_OUTPUT_BYTES))
        stderr_task = asyncio.create_task(_read_bounded(proc.stderr, _NUTRIMATIC_MAX_OUTPUT_BYTES))
        wait_task = asyncio.create_task(proc.wait())
        result_task = asyncio.gather(stdout_task, stderr_task, wait_task)
        try:
            stdout, stderr, _ = await asyncio.wait_for(
                asyncio.shield(result_task),
                timeout=_NUTRIMATIC_DEADLINE_SECONDS,
            )
        except asyncio.TimeoutError:
            await _finish_nutrimatic_cleanup(proc, stdout_task, stderr_task, wait_task)
            logger.warning(
                "nutrimatic timeout pid=%d pgid=%d exit_code=%s duration=%.3f",
                proc.pid, proc.pid, proc.returncode,
                asyncio.get_running_loop().time() - started,
            )
            return HttpResponse("Search timed out", status=504, content_type="text/plain; charset=utf-8")
        except ValueError:
            await _finish_nutrimatic_cleanup(proc, stdout_task, stderr_task, wait_task)
            logger.warning("nutrimatic output_limit pid=%d", proc.pid)
            return HttpResponse("Search unavailable", status=502, content_type="text/plain; charset=utf-8")
    except asyncio.CancelledError:
        if proc is not None:
            cleanup = asyncio.create_task(_finish_nutrimatic_cleanup(proc, stdout_task, stderr_task, wait_task))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
        logger.info(
            "nutrimatic cancelled pid=%s pgid=%s exit_code=%s duration=%.3f",
            proc.pid if proc is not None else "unstarted",
            proc.pid if proc is not None else "unstarted",
            proc.returncode if proc is not None else "unstarted",
            asyncio.get_running_loop().time() - started,
        )
        raise
    except OSError:
        if proc is not None:
            await _finish_nutrimatic_cleanup(proc, stdout_task, stderr_task, wait_task)
        logger.exception("Nutrimatic search process failed")
        return HttpResponse("Search unavailable", status=503, content_type="text/plain; charset=utf-8")
    finally:
        await _nutrimatic_release()

    logger.info("nutrimatic finish pid=%d exit_code=%s duration=%.3f", proc.pid, proc.returncode, asyncio.get_running_loop().time() - started)

    if proc.returncode != 0:
        logger.error(
            "nutrimatic cgi failed pid=%d exit_code=%s stderr_bytes=%d",
            proc.pid, proc.returncode, len(stderr or b""),
        )
        return HttpResponse(
            "Поиск не удалось выполнить. Попробуйте изменить запрос или повторить позже.",
            status=500,
            content_type="text/plain; charset=utf-8",
        )

    body = _strip_cgi_headers(stdout)
    favicon_url = f'{settings.STATIC_URL.rstrip("/")}/img/favicon/favicon.ico'
    body = body.replace(b'href="/favicon.ico"', f'href="{favicon_url}"'.encode())
    return HttpResponse(body, content_type="text/html; charset=utf-8")


async def _finish_nutrimatic_cleanup(proc, *tasks):
    await _terminate_nutrimatic_group(proc, *tasks)
    pending = [task for task in tasks if task is not None]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


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
    """Landing page — no network sync; PDFs/HTML load lazily on click."""
    sync_on = sync_configured(settings)
    manifest_display = _read_booklet_manifest_json() or {}

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


def _cache_booklet_bytes(rel_under_2026: str, body: bytes) -> Path | None:
    """Write fetched bytes under var/eurovision_booklet/2026/<rel>. Returns path or None."""
    rel = (rel_under_2026 or "").strip().strip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    root = Path(settings.BASE_DIR) / "var" / "eurovision_booklet" / "2026"
    dest = root.joinpath(*Path(rel).parts)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(dest)
        return dest
    except OSError:
        logger.info("Eurovision booklet: failed to cache %s", dest, exc_info=True)
        return None


def _booklet_html_loading_snippet() -> bytes:
    """Overlay shown until window load; re-shown on same-booklet navigations."""
    return (
        f"""
<div id="booklet-loading" {_BOOKLET_HTML_LOADING_MARKER} hidden style="display:flex;position:fixed;inset:0;z-index:99999;align-items:center;justify-content:center;flex-direction:column;gap:1rem;background:rgba(15,17,20,.92);color:#e8eaed;font:600 1rem/1.4 system-ui,sans-serif">
  <div style="width:2rem;height:2rem;border:3px solid rgba(255,255,255,.2);border-top-color:#c9a227;border-radius:50%;animation:booklet-spin .7s linear infinite"></div>
  <p style="margin:0">Загрузка, подождите…</p>
  <p style="margin:0;font-weight:400;font-size:.85rem;color:#9aa0a6">Loading, please wait…</p>
</div>
<style>@keyframes booklet-spin{{to{{transform:rotate(360deg)}}}}</style>
<script>
(function(){{
  var el=document.getElementById('booklet-loading');
  if(!el) return;
  function show(){{el.hidden=false;el.style.display='flex';}}
  function hide(){{el.hidden=true;el.style.display='none';}}
  show();
  function done(){{hide();}}
  if(document.readyState==='complete') done();
  else window.addEventListener('load', done);
  setTimeout(done, 30000);
  document.addEventListener('click', function(ev){{
    var a=ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
    if(!a) return;
    var href=a.getAttribute('href')||'';
    if(!href || href.charAt(0)==='#') return;
    if(a.target==='_blank' || a.hasAttribute('download')) return;
    try {{
      var u=new URL(a.href, location.href);
      if(u.origin!==location.origin) return;
      if(u.pathname.indexOf('/eurovision_booklet/')!==0) return;
      show();
    }} catch(e){{}}
  }}, true);
}})();
</script>
""".encode(
            "utf-8"
        )
    )


def _inject_booklet_html_loading(body: bytes) -> bytes:
    if _BOOKLET_HTML_LOADING_MARKER.encode("ascii") in body:
        return body
    snippet = _booklet_html_loading_snippet()
    lower = body.lower()
    idx = lower.rfind(b"</body>")
    if idx >= 0:
        return body[:idx] + snippet + body[idx:]
    return body + snippet


def _http_response_booklet_bytes(body: bytes, ctype: str) -> HttpResponse:
    if ctype.startswith("text/html"):
        body = _inject_booklet_html_loading(body)
    resp = HttpResponse(body, content_type=ctype)
    resp["Cache-Control"] = booklet_cache_control(settings)
    return resp


@require_GET
def eurovision_booklet_pdf(request, filename: str):
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
    if absolute_path:
        resp = FileResponse(open(absolute_path, "rb"), content_type="application/pdf")
        try:
            st = os.stat(absolute_path)
            resp["Cache-Control"] = cc
            resp["Last-Modified"] = http_date(st.st_mtime)
            resp["ETag"] = f'W/"{st.st_size:x}-{int(st.st_mtime):x}"'
        except OSError:
            resp["Cache-Control"] = cc
        return resp
    dist = _booklet_dist_prefix(settings)
    fetched = _fetch_github_raw_booklet(settings, f"{dist}/{filename}")
    if fetched is None:
        raise Http404()
    body, _ctype = fetched
    _cache_booklet_bytes(filename, body)
    resp = FileResponse(
        BytesIO(body),
        content_type="application/pdf",
        as_attachment=False,
        filename=filename,
    )
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


def _guess_booklet_ctype(path_or_name: str | Path) -> str:
    name = Path(path_or_name).name
    ctype, _enc = mimetypes.guess_type(name)
    if not ctype:
        ctype = "application/octet-stream"
    if ctype == "text/html":
        return "text/html; charset=utf-8"
    if ctype == "text/css":
        return "text/css; charset=utf-8"
    if ctype == "application/javascript":
        return "text/javascript; charset=utf-8"
    return ctype


def _file_response_booklet_asset(path: Path) -> HttpResponse:
    ctype = _guess_booklet_ctype(path)
    if ctype.startswith("text/html"):
        try:
            body = path.read_bytes()
        except OSError as e:
            raise Http404() from e
        resp = _http_response_booklet_bytes(body, ctype)
        try:
            stat = path.stat()
            resp["Last-Modified"] = http_date(stat.st_mtime)
            resp["ETag"] = f'W/"{stat.st_size:x}-{int(stat.st_mtime):x}"'
        except OSError:
            pass
        return resp
    resp = FileResponse(path.open("rb"), content_type=ctype)
    stat = path.stat()
    resp["Cache-Control"] = booklet_cache_control(settings)
    resp["Last-Modified"] = http_date(stat.st_mtime)
    resp["ETag"] = f'W/"{stat.st_size:x}-{int(stat.st_mtime):x}"'
    return resp


@require_GET
def eurovision_booklet_html_bundle(request, slug: str, relpath: str = "index.html"):
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
        _cache_booklet_bytes(f"html/{slug}/{rel}", body)
        return _http_response_booklet_bytes(body, ctype)
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
    resolved = _resolve_booklet_shared_asset_file(settings, relpath)
    if resolved is not None:
        return _file_response_booklet_asset(resolved)
    rel = relpath.strip().strip("/")
    if not rel or ".." in Path(rel).parts:
        raise Http404()
    fetched = _fetch_github_raw_booklet(settings, f"assets/{rel}")
    if fetched is None:
        raise Http404()
    body, ctype = fetched
    _cache_booklet_bytes(f"assets/{rel}", body)
    return _http_response_booklet_bytes(body, ctype)
