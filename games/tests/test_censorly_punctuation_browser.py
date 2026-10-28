"""Check punctuation wrapping with actual public tokens and article styles."""

import json
import os
from itertools import product
from pathlib import Path
from unittest import SkipTest, skipUnless

from django.test import SimpleTestCase

from games.puzzles.censorly.article.redact import build_public_view
from games.puzzles.censorly.text.tokenize import build_puzzle_payload

ROOT = Path(__file__).resolve().parents[2]


@skipUnless(os.environ.get('INTEROVES_LIVE_BROWSER_TESTS') == '1', 'Requires Chromium')
class CensorlyPunctuationBrowserTests(SimpleTestCase):
    def test_source_runs_without_spaces_stay_together_at_line_boundaries(self):
        from playwright.sync_api import sync_playwright

        cases = ('(примерно)', '45°),', '29,5', 'км/с', '16/28',
                 '4,6×1018', 'Вояджера−1', '$100', 'R♄')
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                raise SkipTest('Playwright Chromium is not installed')
            browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
            try:
                page = browser.new_page()
                for case in cases:
                    payload = build_puzzle_payload(wiki_title='Тест', body_text=case)
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
                        for width in (20, 30, 40, 50, 60, 80, 100, 120, 160):
                            page.locator('#censorly-body').evaluate(
                                '(body, value) => { body.style.width = value + "px"; }', width,
                            )
                            gaps = page.locator('#censorly-body').evaluate('''body => {
                                // Line top of a token's first and last fragment.
                                const edges = node => {
                                    const range = document.createRange();
                                    range.selectNodeContents(node);
                                    const rects = Array.from(range.getClientRects())
                                        .filter(rect => rect.width > 0);
                                    return [rects[0].top, rects[rects.length - 1].top];
                                };
                                const toks = Array.from(body.querySelectorAll('.censorly-tok'));
                                return toks.slice(1).map((node, index) =>
                                    Math.round(Math.abs(edges(node)[0] - edges(toks[index])[1])));
                            }''')
                            with self.subTest(case=case, won=won, viewport=viewport, width=width):
                                # Text and block masks have slightly different baselines;
                                # a real line break is roughly one full line-height (20+ px).
                                # A mask wider than the line may still wrap inside itself
                                # on narrow layouts; it must not split from its neighbours.
                                self.assertLess(max(gaps), 10)
            finally:
                browser.close()
