"""The interface must not depend on third-party CDNs.

ru.interoves.com serves visitors who cannot reach cdn.jsdelivr.net and similar
hosts, so CSS, JS, fonts and icons ship with the deployment (static/vendor/).
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

STATIC_DIR = Path(settings.BASE_DIR) / 'static'
TEMPLATES_DIR = STATIC_DIR / 'templates'

CDN_HOSTS = (
    'cdn.jsdelivr.net',
    'unpkg.com',
    'cdnjs.cloudflare.com',
    'fonts.googleapis.com',
    'fonts.gstatic.com',
    'ajax.googleapis.com',
    'bootstrapcdn.com',
    'code.jquery.com',
    'use.fontawesome.com',
    'kit.fontawesome.com',
    'cdn.datatables.net',
)

# Browser-fetched references: <script src>, <link href>, CSS url()/@import.
LOADED_URL_RE = re.compile(
    r'<script[^>]+src=["\']([^"\']+)'
    r'|<link[^>]+href=["\']([^"\']+)'
    r'|url\(\s*["\']?((?:https?:)?//[^"\')]+)'
    r'|@import\s+(?:url\()?\s*["\']?((?:https?:)?//[^"\')\s;]+)',
    re.IGNORECASE,
)

# The pre-5.x django-sql-explorer copies under static/explorer/css|js are not
# referenced by the installed explorer 5.3 templates (they load the *.5.3.js
# bundle), so their stale CDN references are never fetched.
UNUSED_LEGACY = (
    STATIC_DIR / 'explorer' / 'css',
    STATIC_DIR / 'explorer' / 'js',
)

VENDOR_CSS = (
    'vendor/phosphor-icons-web/2.1.2/regular/style.css',
    'vendor/phosphor-icons-web/2.1.2/fill/style.css',
)


def _cdn_references(text):
    for match in LOADED_URL_RE.finditer(text):
        url = next(group for group in match.groups() if group)
        if any(host in url for host in CDN_HOSTS):
            yield url


class NoCdnDependencyTests(SimpleTestCase):
    def _files(self, *suffixes):
        for path in STATIC_DIR.rglob('*'):
            if path.suffix not in suffixes or not path.is_file():
                continue
            if any(parent in path.parents for parent in UNUSED_LEGACY):
                continue
            if 'microsites' in path.relative_to(STATIC_DIR).parts:
                # Synced third-party booklet exports, not the site interface.
                continue
            yield path

    def test_templates_and_pages_do_not_load_from_cdns(self):
        for path in self._files('.html', '.txt'):
            text = path.read_text(encoding='utf-8', errors='replace')
            with self.subTest(path=str(path.relative_to(STATIC_DIR))):
                self.assertEqual(list(_cdn_references(text)), [])

    def test_stylesheets_and_scripts_do_not_load_from_cdns(self):
        for path in self._files('.css', '.js'):
            text = path.read_text(encoding='utf-8', errors='replace')
            with self.subTest(path=str(path.relative_to(STATIC_DIR))):
                self.assertEqual(list(_cdn_references(text)), [])

    def test_vendored_icon_css_references_only_shipped_fonts(self):
        for name in VENDOR_CSS:
            css_path = STATIC_DIR / name
            css = css_path.read_text(encoding='utf-8')
            refs = re.findall(r'url\(\s*["\']?([^"\')]+)', css)
            self.assertTrue(any(ref.endswith('.woff2') for ref in refs), name)
            for ref in refs:
                with self.subTest(css=name, ref=ref):
                    self.assertNotRegex(ref, r'^([a-z]+:)?//')
                    self.assertTrue((css_path.parent / ref.split('#')[0]).is_file())

    def test_vendored_packages_keep_their_licenses(self):
        for license_path in (
            'vendor/phosphor-icons-web/2.1.2/LICENSE',
            'vendor/chart.js/4.4.4/LICENSE.md',
        ):
            self.assertIn('MIT', (STATIC_DIR / license_path).read_text(encoding='utf-8'))

    def test_vendored_chart_js_is_the_pinned_version(self):
        head = (STATIC_DIR / 'vendor/chart.js/4.4.4/chart.umd.js').read_text(encoding='utf-8')[:200]
        self.assertIn('Chart.js v4.4.4', head)
