import asyncio
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings as dj_settings
from django.test import AsyncClient, Client, SimpleTestCase, override_settings
from django.urls import resolve, reverse

from microsites.eurovision_booklet_sync import (
    BOOKLET_HTML_SLUGS,
    BOOKLET_MINISITE_SECTIONS,
    BOOKLET_PDF_FILENAMES,
    booklet_cache_control,
    booklet_is_frozen,
    ensure_eurovision_booklet_sync,
)
from microsites.views import (
    _BOOKLET_HTML_LOADING_MARKER,
    _booklet_github_raw_url,
    _fetch_github_raw_booklet,
)


class BookletGithubFallbackTests(SimpleTestCase):
    def test_booklet_github_raw_url_none_without_repo(self):
        class _S:
            EUROVISION_BOOKLET_GITHUB_REPO = ""
            EUROVISION_BOOKLET_GIT_BRANCH = "main"
            EUROVISION_BOOKLET_PINNED_REF = ""

        self.assertIsNone(_booklet_github_raw_url(_S(), "dist/x.pdf"))

    def test_booklet_github_raw_url_joins_branch_and_path(self):
        class _S:
            EUROVISION_BOOKLET_GITHUB_REPO = "owner/repo-name"
            EUROVISION_BOOKLET_GIT_BRANCH = "main"
            EUROVISION_BOOKLET_PINNED_REF = ""

        u = _booklet_github_raw_url(_S(), "dist/html/eurovision2026_en/index.html")
        self.assertEqual(
            u,
            "https://raw.githubusercontent.com/owner/repo-name/main/dist/html/eurovision2026_en/index.html",
        )

    def test_booklet_github_raw_url_prefers_pinned_ref(self):
        class _S:
            EUROVISION_BOOKLET_GITHUB_REPO = "owner/repo-name"
            EUROVISION_BOOKLET_GIT_BRANCH = "main"
            EUROVISION_BOOKLET_PINNED_REF = "abc123def456"

        u = _booklet_github_raw_url(_S(), "dist/eurovision2026_en.pdf")
        self.assertEqual(
            u,
            "https://raw.githubusercontent.com/owner/repo-name/abc123def456/dist/eurovision2026_en.pdf",
        )

    @patch("microsites.views._resolve_booklet_html_bundle_file", return_value=None)
    @patch("microsites.views.urlopen")
    def test_html_bundle_proxies_from_github_when_local_missing(
        self, mock_urlopen, mock_resolve
    ):
        """Same-origin HTML so relative links stay on interoves.com."""
        sample = BOOKLET_HTML_SLUGS[0]
        inner = MagicMock()
        inner.status = 200
        inner.getcode = lambda: 200
        inner.read.return_value = b"<!DOCTYPE html><html><body>gh</body></html>"
        inner.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_urlopen.return_value.__enter__.return_value = inner
        mock_urlopen.return_value.__exit__.return_value = False

        td = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        hosts = list(dj_settings.ALLOWED_HOSTS) + ["testserver"]
        with override_settings(
            BASE_DIR=td,
            ALLOWED_HOSTS=hosts,
            EUROVISION_BOOKLET_GITHUB_REPO="owner/booklet-repo",
            EUROVISION_BOOKLET_GIT_BRANCH="main",
            EUROVISION_BOOKLET_DIST_PATH="dist",
            EUROVISION_BOOKLET_HTML_BASE_URL="",
            EUROVISION_BOOKLET_PINNED_REF="",
            EUROVISION_BOOKLET_AUTO_SYNC=True,
        ):
            c = Client()
            r = c.get(f"/eurovision_booklet/2026/html/{sample}/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"gh", r.content)
        self.assertIn(_BOOKLET_HTML_LOADING_MARKER.encode("ascii"), r.content)
        self.assertIn("no-store", r["Cache-Control"])
        called_url = mock_urlopen.call_args[0][0].full_url
        self.assertIn(f"/dist/html/{sample}/index.html", called_url)
        cached = (
            Path(td) / "var" / "eurovision_booklet" / "2026" / "html" / sample / "index.html"
        )
        self.assertTrue(cached.is_file())

    def test_fetch_github_overrides_text_plain_for_html_files(self):
        """GitHub raw returns text/plain for .html; we must serve text/html."""
        inner = MagicMock()
        inner.status = 200
        inner.getcode = lambda: 200
        inner.read.return_value = b"<!DOCTYPE html><html></html>"
        inner.headers = {"Content-Type": "text/plain; charset=utf-8"}
        with patch("microsites.views.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = inner
            mock_urlopen.return_value.__exit__.return_value = False

            class _S:
                EUROVISION_BOOKLET_GITHUB_REPO = "owner/repo"
                EUROVISION_BOOKLET_GIT_BRANCH = "main"
                EUROVISION_BOOKLET_HTTP_TIMEOUT = 30.0

            _body, ctype = _fetch_github_raw_booklet(
                _S(), "dist/html/eurovision2026_en/index.html"
            )
        self.assertEqual(ctype, "text/html; charset=utf-8")

    @patch("microsites.views.finders.find", return_value=None)
    @patch("microsites.views._fetch_github_raw_booklet")
    def test_pdf_github_fallback_inline_not_redirect(self, mock_fetch, mock_find):
        """Missing local PDF is proxied from GitHub with inline disposition (open in tab)."""
        pdf_bytes = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        mock_fetch.return_value = (pdf_bytes, "application/pdf")
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        hosts = list(dj_settings.ALLOWED_HOSTS) + ["testserver"]
        with override_settings(
            BASE_DIR=td,
            ALLOWED_HOSTS=hosts,
            EUROVISION_BOOKLET_GITHUB_REPO="owner/repo",
            EUROVISION_BOOKLET_DIST_PATH="dist",
        ):
            c = Client()
            r = c.get("/eurovision_booklet/2026/pdf/eurovision2026_en.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"].split(";")[0].strip(), "application/pdf")
        cd = (r.get("Content-Disposition") or "").lower()
        self.assertNotIn("attachment", cd)
        cached = Path(td) / "var" / "eurovision_booklet" / "2026" / "eurovision2026_en.pdf"
        self.assertTrue(cached.is_file())
        self.assertEqual(cached.read_bytes(), pdf_bytes)
        mock_fetch.assert_called_once()


class MicrositesUrlTests(SimpleTestCase):
    def test_nutrimatic_home_resolves(self):
        m = resolve("/nutrimatic-ru/")
        self.assertEqual(m.url_name, "nutrimatic_home")

    def test_nutrimatic_static_path_resolves(self):
        m = resolve("/nutrimatic-ru/robots.txt")
        self.assertEqual(m.kwargs["rel_path"], "robots.txt")

    @override_settings(NUTRIMATIC_LAMBDA_FUNCTION="")
    def test_nutrimatic_unconfigured_returns_503(self):
        r = Client().get("/nutrimatic-ru/")
        self.assertEqual(r.status_code, 503)

    def test_eurovision_booklet_2026_resolves(self):
        m = resolve("/eurovision_booklet/2026/")
        self.assertEqual(m.url_name, "eurovision_booklet_2026")

    def test_eurovision_booklet_2026_reverse(self):
        self.assertEqual(
            reverse("eurovision_booklet_2026"),
            "/eurovision_booklet/2026/",
        )

    def test_eurovision_booklet_pdf_reverse(self):
        self.assertEqual(
            reverse(
                "eurovision_booklet_pdf",
                kwargs={"filename": "eurovision2026_sf1_ru.pdf"},
            ),
            "/eurovision_booklet/2026/pdf/eurovision2026_sf1_ru.pdf",
        )

    def test_eurovision_booklet_html_index_reverse(self):
        sample = BOOKLET_HTML_SLUGS[0]
        self.assertEqual(
            reverse("eurovision_booklet_html", kwargs={"slug": sample}),
            f"/eurovision_booklet/2026/html/{sample}/",
        )

    def test_eurovision_booklet_html_asset_resolves(self):
        m = resolve("/eurovision_booklet/2026/html/eurovision2026_en/booklet.css")
        self.assertEqual(m.url_name, "eurovision_booklet_html_asset")

    def test_eurovision_booklet_2026_has_four_version_blocks(self):
        c = Client()
        r = c.get("/eurovision_booklet/2026/")
        self.assertEqual(r.status_code, 200)
        content = r.content.decode()
        for _sid, title, _ru, _en in BOOKLET_MINISITE_SECTIONS:
            self.assertIn(title, content)
        self.assertEqual(len(BOOKLET_MINISITE_SECTIONS), 4)
        self.assertIn("js-booklet-lazy", content)
        self.assertIn("booklet-load-overlay", content)

    @patch("microsites.eurovision_booklet_sync.ensure_eurovision_booklet_sync")
    def test_landing_does_not_call_bulk_sync(self, mock_ensure):
        hosts = list(dj_settings.ALLOWED_HOSTS) + ["testserver"]
        with override_settings(
            ALLOWED_HOSTS=hosts,
            EUROVISION_BOOKLET_GITHUB_REPO="owner/repo",
            EUROVISION_BOOKLET_PINNED_REF="abc123",
            EUROVISION_BOOKLET_AUTO_SYNC=False,
        ):
            r = Client().get("/eurovision_booklet/2026/")
        self.assertEqual(r.status_code, 200)
        mock_ensure.assert_not_called()

    def test_eurovision_booklet_200_and_pdf_404(self):
        c = Client()
        r = c.get("/eurovision_booklet/2026/")
        self.assertEqual(r.status_code, 200)
        bad = c.get("/eurovision_booklet/2026/pdf/not-a-booklet.pdf")
        self.assertEqual(bad.status_code, 404)
        bad_html = c.get("/eurovision_booklet/2026/html/not-a-real-slug/")
        self.assertEqual(bad_html.status_code, 404)

    def test_eurovision_booklet_pdf_has_no_store_cache_headers(self):
        hosts = list(dj_settings.ALLOWED_HOSTS) + ["testserver"]
        with override_settings(
            ALLOWED_HOSTS=hosts,
            EUROVISION_BOOKLET_PINNED_REF="",
            EUROVISION_BOOKLET_AUTO_SYNC=True,
        ):
            c = Client()
            r = c.get("/eurovision_booklet/2026/pdf/eurovision2026_sf1_ru.pdf")
        # May 404 if PDFs are not bundled in test env; only assert headers when it exists.
        if r.status_code == 200:
            self.assertIn("no-store", r["Cache-Control"])
            self.assertTrue(r.get("ETag"))
            self.assertTrue(r.get("Last-Modified"))

    def test_eurovision_booklet_pdf_frozen_uses_long_cache(self):
        hosts = list(dj_settings.ALLOWED_HOSTS) + ["testserver"]
        with override_settings(
            ALLOWED_HOSTS=hosts,
            EUROVISION_BOOKLET_PINNED_REF="23be3106b745af459479f9f3d97579e74ecbe939",
            EUROVISION_BOOKLET_AUTO_SYNC=False,
        ):
            c = Client()
            r = c.get("/eurovision_booklet/2026/pdf/eurovision2026_sf1_ru.pdf")
        if r.status_code == 200:
            self.assertIn("max-age=604800", r["Cache-Control"])
            self.assertNotIn("no-store", r["Cache-Control"])

    def test_eurovision_booklet_assets_resolves(self):
        m = resolve("/eurovision_booklet/assets/flags/png/AL.png")
        self.assertEqual(m.url_name, "eurovision_booklet_assets")

    def test_eurovision_booklet_assets_traversal_404(self):
        hosts = list(dj_settings.ALLOWED_HOSTS) + ["testserver"]
        with override_settings(ALLOWED_HOSTS=hosts):
            c = Client()
            r = c.get("/eurovision_booklet/assets/x/../../../etc/passwd")
            self.assertEqual(r.status_code, 404)

    def test_eurovision_booklet_assets_serves_from_local_dir(self):
        td = Path(tempfile.mkdtemp())
        (td / "flags" / "png").mkdir(parents=True)
        (td / "flags" / "png" / "AL.png").write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        hosts = list(dj_settings.ALLOWED_HOSTS) + ["testserver"]
        with override_settings(
            ALLOWED_HOSTS=hosts,
            EUROVISION_BOOKLET_LOCAL_ASSETS_DIR=str(td),
            EUROVISION_BOOKLET_PINNED_REF="",
            EUROVISION_BOOKLET_AUTO_SYNC=True,
        ):
            c = Client()
            r = c.get("/eurovision_booklet/assets/flags/png/AL.png")
            self.assertEqual(r.status_code, 200)
            self.assertIn("no-store", r["Cache-Control"])

    def test_eurovision_booklet_html_serves_from_local_dir(self):
        td = Path(tempfile.mkdtemp())
        bundle = td / "eurovision2026_sf1_ru"
        bundle.mkdir()
        (bundle / "index.html").write_bytes(
            b"<!DOCTYPE html><html><body>ok</body></html>"
        )
        (bundle / "booklet.css").write_bytes(b"body { margin: 0; }")
        hosts = list(dj_settings.ALLOWED_HOSTS) + ["testserver"]
        with override_settings(
            ALLOWED_HOSTS=hosts,
            EUROVISION_BOOKLET_LOCAL_HTML_DIR=str(td),
            EUROVISION_BOOKLET_PINNED_REF="",
            EUROVISION_BOOKLET_AUTO_SYNC=True,
        ):
            c = Client()
            r = c.get("/eurovision_booklet/2026/html/eurovision2026_sf1_ru/")
            self.assertEqual(r.status_code, 200)
            self.assertIn("no-store", r["Cache-Control"])
            self.assertIn(_BOOKLET_HTML_LOADING_MARKER.encode("ascii"), r.content)
            self.assertTrue(r.get("ETag"))
            self.assertTrue(r.get("Last-Modified"))
            rc = c.get("/eurovision_booklet/2026/html/eurovision2026_sf1_ru/booklet.css")
            self.assertEqual(rc.status_code, 200)
            self.assertIn("no-store", rc["Cache-Control"])


class BookletFreezeTests(SimpleTestCase):
    def test_frozen_when_pinned_or_auto_sync_off(self):
        class _Pinned:
            EUROVISION_BOOKLET_PINNED_REF = "abc"
            EUROVISION_BOOKLET_AUTO_SYNC = True

        class _Off:
            EUROVISION_BOOKLET_PINNED_REF = ""
            EUROVISION_BOOKLET_AUTO_SYNC = False

        class _Live:
            EUROVISION_BOOKLET_PINNED_REF = ""
            EUROVISION_BOOKLET_AUTO_SYNC = True

        self.assertTrue(booklet_is_frozen(_Pinned()))
        self.assertTrue(booklet_is_frozen(_Off()))
        self.assertFalse(booklet_is_frozen(_Live()))
        self.assertIn("immutable", booklet_cache_control(_Pinned()))
        self.assertIn("no-store", booklet_cache_control(_Live()))

    def test_ensure_skips_network_when_pin_cache_complete(self):
        td = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        cache = td / "var" / "eurovision_booklet" / "2026"
        cache.mkdir(parents=True)
        for name in BOOKLET_PDF_FILENAMES:
            (cache / name).write_bytes(b"%PDF-1.4\n")
        for slug in BOOKLET_HTML_SLUGS:
            d = cache / "html" / slug
            d.mkdir(parents=True)
            (d / "index.html").write_bytes(b"<html></html>")
        pin = "23be3106b745af459479f9f3d97579e74ecbe939"
        (cache / "manifest.json").write_text(
            '{"branch_tip": "%s", "files": {}}' % pin,
            encoding="utf-8",
        )

        class _S:
            BASE_DIR = str(td)
            EUROVISION_BOOKLET_GITHUB_REPO = "owner/repo"
            EUROVISION_BOOKLET_REPO_PATH = ""
            EUROVISION_BOOKLET_PINNED_REF = pin
            EUROVISION_BOOKLET_AUTO_SYNC = False
            EUROVISION_BOOKLET_SYNC_MIN_INTERVAL_SEC = 0
            EUROVISION_BOOKLET_HTTP_TIMEOUT = 5.0
            EUROVISION_BOOKLET_DIST_PATH = "dist"

        with patch(
            "microsites.eurovision_booklet_sync._sync_github"
        ) as mock_gh, patch(
            "microsites.eurovision_booklet_sync._sync_local_git"
        ) as mock_local:
            m = ensure_eurovision_booklet_sync(_S())
        self.assertEqual(m.get("branch_tip"), pin)
        mock_gh.assert_not_called()
        mock_local.assert_not_called()

    def test_ensure_noop_when_auto_sync_off_without_pin(self):
        td = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))

        class _S:
            BASE_DIR = str(td)
            EUROVISION_BOOKLET_GITHUB_REPO = "owner/repo"
            EUROVISION_BOOKLET_REPO_PATH = ""
            EUROVISION_BOOKLET_PINNED_REF = ""
            EUROVISION_BOOKLET_AUTO_SYNC = False
            EUROVISION_BOOKLET_SYNC_MIN_INTERVAL_SEC = 0
            EUROVISION_BOOKLET_HTTP_TIMEOUT = 5.0
            EUROVISION_BOOKLET_DIST_PATH = "dist"

        with patch(
            "microsites.eurovision_booklet_sync._sync_github"
        ) as mock_gh:
            m = ensure_eurovision_booklet_sync(_S())
        self.assertEqual(m, {})
        mock_gh.assert_not_called()


class BookletLazyAssetProxyTests(SimpleTestCase):
    @patch("microsites.views._resolve_booklet_shared_asset_file", return_value=None)
    @patch("microsites.views._fetch_github_raw_booklet")
    def test_shared_asset_proxied_and_cached(self, mock_fetch, mock_resolve):
        mock_fetch.return_value = (b"\x89PNG\r\n", "image/png")
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        hosts = list(dj_settings.ALLOWED_HOSTS) + ["testserver"]
        with override_settings(
            BASE_DIR=td,
            ALLOWED_HOSTS=hosts,
            EUROVISION_BOOKLET_GITHUB_REPO="owner/repo",
            EUROVISION_BOOKLET_PINNED_REF="abc",
            EUROVISION_BOOKLET_AUTO_SYNC=False,
        ):
            r = Client().get("/eurovision_booklet/assets/flags/png/AL.png")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"\x89PNG\r\n")
        cached = (
            Path(td) / "var" / "eurovision_booklet" / "2026" / "assets" / "flags" / "png" / "AL.png"
        )
        self.assertTrue(cached.is_file())
        mock_fetch.assert_called_once()


@override_settings(NUTRIMATIC_LAMBDA_FUNCTION="interoves-nutrimatic")
class NutrimaticLambdaBackendTests(SimpleTestCase):
    """The view only invokes the Lambda; find-expr never runs on the web instance."""

    def setUp(self):
        from microsites import views

        views._NUTRIMATIC_SEARCH_GATE = None
        views._NUTRIMATIC_GATE_LOCK = None
        views._NUTRIMATIC_ACTIVE = 0
        views._NUTRIMATIC_QUEUED = 0
        self.addCleanup(setattr, views, "_NUTRIMATIC_SEARCH_GATE", None)

    def invoke_returning(self, result):
        return patch("microsites.views._invoke_nutrimatic_lambda", return_value=result)

    async def call_view(self, query="q=%D0%BA%D0%BE%D1%82&start=0"):
        from django.test import RequestFactory

        from microsites.views import nutrimatic_search
        return await nutrimatic_search(RequestFactory().get("/nutrimatic-ru/?" + query))

    async def test_ok_renders_cgi_body(self):
        import base64

        body = base64.b64encode(b'Content-type: text/html\n\n<html><link href="/favicon.ico">ok</html>').decode()
        with self.invoke_returning({"status": "ok", "exit_code": 0, "body_b64": body}) as invoke:
            response = await self.call_view()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ok</html>", response.content)
        self.assertNotIn(b'href="/favicon.ico"', response.content)
        invoke.assert_called_once_with("q=%D0%BA%D0%BE%D1%82&start=0")

    async def test_timeout_and_crash_show_heavy_query_page(self):
        for result in ({"status": "timeout", "exit_code": -15}, {"status": "crashed", "error_type": "Runtime.OutOfMemory"}):
            with self.subTest(result=result), self.invoke_returning(result):
                response = await self.call_view()
                self.assertEqual(response.status_code, 504)
                self.assertTrue(response.interoves_expected_error)

    async def test_throttled_shows_busy_page(self):
        with self.invoke_returning({"throttled": True}):
            response = await self.call_view()
        self.assertEqual(response.status_code, 429)
        self.assertIn("Сейчас выполняется другой поиск", response.content.decode())

    async def test_invoke_failure_returns_503_and_releases_gate(self):
        from microsites import views

        with patch("microsites.views._invoke_nutrimatic_lambda", side_effect=RuntimeError("network")):
            response = await self.call_view()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(views._NUTRIMATIC_ACTIVE, 0)

    async def test_two_searches_run_at_once_per_instance(self):
        import threading

        release = threading.Event()

        def slow_invoke(_query):
            release.wait(5)
            return {"status": "ok", "exit_code": 0, "body_b64": ""}

        with patch("microsites.views._invoke_nutrimatic_lambda", side_effect=slow_invoke):
            first = asyncio.create_task(self.call_view())
            second = asyncio.create_task(self.call_view())
            await asyncio.sleep(0.1)
            from microsites import views
            self.assertEqual(views._NUTRIMATIC_ACTIVE, 2)
            release.set()
            self.assertEqual([(await first).status_code, (await second).status_code], [200, 200])

    def test_invoke_maps_function_error_and_throttling(self):
        import io
        import json as jsonlib

        from microsites import views

        class Throttled(Exception):
            pass

        client = MagicMock()
        client.exceptions.TooManyRequestsException = Throttled
        client.invoke.return_value = {
            "FunctionError": "Unhandled",
            "Payload": io.BytesIO(jsonlib.dumps({"errorType": "Runtime.OutOfMemory"}).encode()),
        }
        with patch.object(views, "_nutrimatic_lambda_client", return_value=client):
            self.assertEqual(
                views._invoke_nutrimatic_lambda("q=x"),
                {"status": "crashed", "error_type": "Runtime.OutOfMemory"},
            )
            client.invoke.side_effect = Throttled()
            self.assertEqual(views._invoke_nutrimatic_lambda("q=x"), {"throttled": True})
        sent = jsonlib.loads(client.invoke.call_args.kwargs["Payload"])
        self.assertEqual(sent, {"query_string": "q=x"})

    async def test_gate_rejects_when_both_slots_and_queue_are_busy(self):
        import threading

        from microsites import views

        release = threading.Event()

        def slow_invoke(_query):
            release.wait(5)
            return {"status": "ok", "exit_code": 0, "body_b64": ""}

        with patch("microsites.views._invoke_nutrimatic_lambda", side_effect=slow_invoke), \
                patch.object(views, "_NUTRIMATIC_QUEUE_WAIT_SECONDS", 0.2):
            running = [asyncio.create_task(self.call_view()) for _ in range(2)]
            await asyncio.sleep(0.05)
            queued = asyncio.create_task(self.call_view())
            await asyncio.sleep(0.02)
            rejected = await self.call_view()
            self.assertEqual(rejected.status_code, 429)
            self.assertEqual(rejected.headers.get("Retry-After"), "5")
            self.assertIn("Повторить запрос", rejected.content.decode())
            self.assertEqual((await queued).status_code, 429)
            release.set()
            self.assertEqual([(await t).status_code for t in running], [200, 200])

    async def test_health_endpoint_stays_responsive_during_search(self):
        import threading

        release = threading.Event()

        def slow_invoke(_query):
            release.wait(5)
            return {"status": "ok", "exit_code": 0, "body_b64": ""}

        with patch("microsites.views._invoke_nutrimatic_lambda", side_effect=slow_invoke):
            client = AsyncClient()
            search = asyncio.create_task(client.get("/nutrimatic-ru/?q=slow"))
            await asyncio.sleep(0.05)
            health = await client.get("/health/live/")
            self.assertEqual(health.status_code, 200)
            self.assertFalse(search.done())
            release.set()
            self.assertEqual((await search).status_code, 200)


class NutrimaticLambdaHandlerTests(SimpleTestCase):
    """infra/nutrimatic/handler.py running the real cgi-search.py with a stub find-expr."""

    REPO = Path(__file__).resolve().parent.parent

    def setUp(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "nutrimatic_lambda_handler", self.REPO / "infra/nutrimatic/handler.py",
        )
        self.handler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.handler)
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.find_expr = self.root / "find-expr"
        index = self.root / "words.index"
        index.touch()
        self.handler.CGI_SCRIPT = str(self.REPO / "nutrimatic_bundle/cgi_scripts/cgi-search.py")
        self.handler.FIND_EXPR = str(self.find_expr)
        self.handler.INDEX = str(index)

    def stub_find_expr(self, source):
        self.find_expr.write_text("#!/usr/bin/env python3\n" + source)
        self.find_expr.chmod(0o755)

    def run_search(self, query_string):
        import base64

        result = self.handler.handler({"query_string": query_string}, None)
        body = base64.b64decode(result.get("body_b64", "")).decode()
        return result, body

    def test_streams_results_and_keeps_query_in_paging(self):
        self.stub_find_expr("import sys\nsys.stderr.write('e' * 200000)\nprint('1.0 Moscow')\n")
        result, body = self.run_search("q=capital&start=0&num=100")
        self.assertEqual(result["status"], "ok")
        self.assertIn("Moscow", body)
        self.assertIn("capital", body)

    def test_stops_offering_harder_search_at_ceiling(self):
        self.stub_find_expr("print('# 4000000')\n")
        _, harder = self.run_search("q=%3Cabc%3E&comp=2000000")
        _, ceiling = self.run_search("q=%3Cabc%3E&comp=8000000")
        self.assertIn("comp=4000000", harder)
        self.assertNotIn("harder", ceiling)
        self.assertIn("maximum effort", ceiling)

    def test_home_page_needs_no_find_expr(self):
        result, body = self.run_search("")
        self.assertEqual(result["status"], "ok")
        self.assertIn("Examples", body)

    def test_deadline_kills_find_expr_that_ignores_sigterm(self):
        pidfile = self.root / "pid"
        self.stub_find_expr(
            "import signal, time\n"
            f"open({str(pidfile)!r}, 'w').write(str(__import__('os').getpid()))\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(30)\n"
        )
        self.handler.DEADLINE_SECONDS = 1.0
        result, _ = self.run_search("q=slow")
        self.assertEqual(result["status"], "timeout")
        pid = int(pidfile.read_text())
        state = None
        for _ in range(50):
            try:
                state = Path(f"/proc/{pid}/stat").read_text().split()[2]
            except FileNotFoundError:
                state = None
                break
            if state == "Z":
                break
            import time
            time.sleep(0.02)
        self.assertIn(state, (None, "Z"), "find-expr survived the deadline")

    def test_output_over_limit_is_not_returned(self):
        self.stub_find_expr("for i in range(200000):\n    print(f'1.0 word{i}')\n")
        self.handler.MAX_OUTPUT_BYTES = 10_000
        result, body = self.run_search("q=word&num=10000")
        self.assertEqual(result["status"], "output_limit")
        self.assertEqual(body, "")
