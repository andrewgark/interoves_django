"""Run site_version.js's JS tests (hint, switch, Metrika goals) with the Django tests."""
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

JS_DIR = Path(settings.BASE_DIR) / 'static' / 'js'


class SiteVersionJsTests(SimpleTestCase):
    def _run(self, name):
        node = shutil.which('node')
        if node is None:
            self.skipTest('node is not installed')
        result = subprocess.run([node, str(JS_DIR / name)], capture_output=True, text=True, timeout=120)
        self.assertEqual(
            result.returncode, 0,
            msg='{} failed:\n{}\n{}'.format(name, result.stdout[-4000:], result.stderr[-4000:]),
        )
        self.assertIn('{}: ok'.format(name), result.stdout)

    def test_site_version_js(self):
        self._run('site_version.test.js')

    def test_metrika_helper_js(self):
        # The delivery event site_version.js waits for before leaving the site.
        self._run('yandex_metrika_helper.test.js')
