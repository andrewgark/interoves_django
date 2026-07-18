import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings as dj_settings
from django.test import Client, SimpleTestCase, override_settings
from django.urls import resolve, reverse

from microsites.eurovision_booklet_sync import (
    BOOKLET_HTML_SLUGS,
    BOOKLET_MINISITE_SECTIONS,
    BOOKLET_PDF_FILENAMES,
    booklet_cache_control,
    booklet_is_frozen,
    ensure_eurovision_booklet_sync,
)
from microsites.views import _booklet_github_raw_url, _fetch_github_raw_booklet


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
    @patch("microsites.views._touch_eurovision_booklet_sync")
    def test_html_bundle_proxies_from_github_when_local_missing(
        self, mock_touch, mock_urlopen, mock_resolve
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

        hosts = list(dj_settings.ALLOWED_HOSTS) + ["testserver"]
        with override_settings(
            ALLOWED_HOSTS=hosts,
            EUROVISION_BOOKLET_GITHUB_REPO="owner/booklet-repo",
            EUROVISION_BOOKLET_GIT_BRANCH="main",
            EUROVISION_BOOKLET_DIST_PATH="dist",
            EUROVISION_BOOKLET_HTML_BASE_URL="",
        ):
            c = Client()
            r = c.get(f"/eurovision_booklet/2026/html/{sample}/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"gh", r.content)
        self.assertIn("no-store", r["Cache-Control"])
        called_url = mock_urlopen.call_args[0][0].full_url
        self.assertIn(f"/dist/html/{sample}/index.html", called_url)

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
    @patch("microsites.views._touch_eurovision_booklet_sync")
    def test_pdf_github_fallback_inline_not_redirect(
        self, mock_touch, mock_fetch, mock_find
    ):
        """Missing local PDF is proxied from GitHub with inline disposition (open in tab)."""
        mock_fetch.return_value = (b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n", "application/pdf")
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


class MicrositesUrlTests(SimpleTestCase):
    def test_nutrimatic_home_resolves(self):
        m = resolve("/nutrimatic-ru/")
        self.assertEqual(m.url_name, "nutrimatic_home")

    def test_nutrimatic_static_path_resolves(self):
        m = resolve("/nutrimatic-ru/robots.txt")
        self.assertEqual(m.kwargs["rel_path"], "robots.txt")

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
