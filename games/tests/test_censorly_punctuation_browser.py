"""Check punctuation wrapping with actual public tokens and article styles."""

import json
import os
from itertools import product
from pathlib import Path
from unittest import SkipTest, skipUnless

from django.test import SimpleTestCase

from games.censorly.redact import build_public_view
from games.censorly.tokenize import build_puzzle_payload


ROOT = Path(__file__).resolve().parents[2]


@skipUnless(os.environ.get('INTEROVES_LIVE_BROWSER_TESTS') == '1', 'Requires Chromium')
class CensorlyPunctuationBrowserTests(SimpleTestCase):
    def test_parentheses_stay_with_words_at_line_boundaries(self):
        from playwright.sync_api import sync_playwright

        payload = build_puzzle_payload(
            wiki_title='Немецкая овчарка', body_text='Собака (примерно 45°), собака',
        )
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                raise SkipTest('Playwright Chromium is not installed')
            browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
            try:
                page = browser.new_page()
                for won, viewport in product((False, True), (560, 800, 1000)):
                    page.set_viewport_size({'width': viewport, 'height': 800})
                    state = build_public_view(payload, won=won)
                    page.set_content(
                        '<main id="censorly-root" class="censorly">'
                        '<div id="censorly-title"></div>'
                        '<div id="censorly-body" class="censorly__body"></div></main>'
                        '<script id="censorly-bootstrap" type="application/json">'
                        + json.dumps(state) + '</script>'
                    )
                    page.add_style_tag(path=str(ROOT / 'static/css/censorly.css'))
                    page.add_script_tag(path=str(ROOT / 'static/js/censorly_punctuation.js'))
                    page.add_script_tag(path=str(ROOT / 'static/js/censorly_play.js'))
                    # Exercise both inline mobile masks and desktop inline-block masks.
                    for width in range(120, 401, 7):
                        page.locator('#censorly-body').evaluate(
                            '(body, width) => { body.style.width = width + "px"; }', width,
                        )
                        positions = page.locator('#censorly-body').evaluate('''body => {
                            const nodes = Array.from(body.querySelectorAll('.censorly-tok'));
                            function line(node) {
                                const range = document.createRange();
                                range.selectNodeContents(node);
                                return range.getBoundingClientRect().top;
                            }
                            const opening = nodes.findIndex(node => node.textContent === '(');
                            const closing = nodes.findIndex(node => node.textContent === '),');
                            return {
                                opening: line(nodes[opening]), next: line(nodes[opening + 1]),
                                closing: line(nodes[closing]), previous: line(nodes[closing - 1]),
                                number: line(nodes[closing - 2]),
                                degreeVisible: nodes[closing - 1].textContent === '°',
                            };
                        }''')
                        with self.subTest(won=won, viewport=viewport, width=width):
                            self.assertLess(abs(positions['opening'] - positions['next']), 5)
                            self.assertLess(abs(positions['closing'] - positions['previous']), 5)
                            self.assertLess(abs(positions['closing'] - positions['number']), 5)
                            self.assertTrue(positions['degreeVisible'])
            finally:
                browser.close()
