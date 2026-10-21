"""Run the daily solve timer's JS test suite as part of the Django tests."""
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

TIMER_TEST = Path(settings.BASE_DIR) / 'static' / 'js' / 'daily_solve_timer.test.js'


class DailySolveTimerJsTests(SimpleTestCase):
    def test_timer_js_suite_passes(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('node is not installed')
        result = subprocess.run(
            [node, str(TIMER_TEST)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            result.returncode, 0,
            msg='daily_solve_timer.test.js failed:\n{}\n{}'.format(
                result.stdout[-4000:], result.stderr[-4000:],
            ),
        )
        self.assertIn('daily_solve_timer tests ok', result.stdout)
