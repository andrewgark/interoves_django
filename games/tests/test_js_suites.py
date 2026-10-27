"""Run every static/js/*.test.js suite as part of the Django tests.

Each suite is a plain node script that exits non-zero on failure. The timer
suite has its own test with an extra completion marker check.
"""
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

JS_DIR = Path(settings.BASE_DIR) / 'static' / 'js'
SEPARATELY_RUN = {'daily_solve_timer.test.js'}


class JsSuitesTests(SimpleTestCase):
    def test_js_suites_pass(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('node is not installed')
        suites = sorted(
            path for path in JS_DIR.glob('*.test.js') if path.name not in SEPARATELY_RUN
        )
        self.assertTrue(suites)
        for suite in suites:
            with self.subTest(suite=suite.name):
                result = subprocess.run(
                    [node, str(suite)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(JS_DIR),
                )
                self.assertEqual(
                    result.returncode, 0,
                    msg='{} failed:\n{}\n{}'.format(
                        suite.name, result.stdout[-4000:], result.stderr[-4000:],
                    ),
                )
