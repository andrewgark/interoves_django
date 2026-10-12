"""Browser smoke coverage for the Censorly word-report interaction."""

import os
from pathlib import Path
from unittest import SkipTest, skipUnless

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]


@skipUnless(
    os.environ.get('INTEROVES_LIVE_BROWSER_TESTS') == '1',
    'Set INTEROVES_LIVE_BROWSER_TESTS=1 to run Chromium verification.',
)
class CensorlyWordReportBrowserTests(SimpleTestCase):
    def test_opened_word_report_reason_and_submit(self):
        try:
            from playwright.sync_api import expect, sync_playwright
        except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
            raise SkipTest('Playwright Python package is not installed') from exc

        script = (ROOT / 'static/js/censorly_word_report.js').read_text(encoding='utf-8')
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                raise SkipTest('Playwright Chromium is not installed')
            try:
                browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
            except Exception as exc:  # pragma: no cover - depends on host sandbox policy
                raise SkipTest('Playwright Chromium cannot launch in this environment') from exc
            try:
                page = browser.new_page()
                page.set_content(
                    '''
                    <main id="censorly-root" data-word-report-url="/censorly/word-report/"
                          data-game-id="censorly" data-context-token="ctx-test">
                      <button class="censorly-tok--reportable" data-id="42"
                              data-surface="Поддержка">Поддержка</button>
                    </main>
                    <div id="censorly-word-report-modal" hidden>
                      <div id="censorly-word-report-target"></div>
                      <div id="censorly-word-report-other-wrap" hidden>
                        <label id="censorly-word-report-other-label" for="censorly-word-report-other"></label>
                        <input id="censorly-word-report-other">
                      </div>
                      <label><input type="radio" name="censorly-word-report-reason" value="FALSE_POSITIVE">Лишнее</label>
                      <label><input type="radio" name="censorly-word-report-reason" value="FALSE_NEGATIVE">Пропущено</label>
                      <label><input type="radio" name="censorly-word-report-reason" value="OTHER">Другое</label>
                      <textarea id="censorly-word-report-comment"></textarea>
                      <p id="censorly-word-report-error" hidden></p>
                      <p id="censorly-word-report-success" hidden>Спасибо</p>
                      <button type="button" data-censorly-word-report-submit>Отправить</button>
                    </div>
                    <script>
                      window.fetch = function (url, options) {
                        window.__reportRequest = {url: url, options: options};
                        return Promise.resolve(new Response(JSON.stringify({ok: true}), {
                          status: 201,
                          headers: {'Content-Type': 'application/json'}
                        }));
                      };
                    </script>
                    '''
                )
                page.add_script_tag(content=script)

                page.locator('.censorly-tok--reportable').click()
                expect(page.locator('#censorly-word-report-modal')).to_be_visible()
                expect(page.locator('#censorly-word-report-target')).to_have_text('Поддержка')

                page.locator('input[value="OTHER"]').check()
                expect(page.locator('#censorly-word-report-other-wrap')).to_be_hidden()
                page.locator('input[value="FALSE_POSITIVE"]').check()
                expect(page.locator('#censorly-word-report-other-wrap')).to_be_visible()
                page.locator('#censorly-word-report-other').fill('Держать')
                page.locator('#censorly-word-report-comment').fill('Проверка smoke workflow')
                page.locator('[data-censorly-word-report-submit]').click()

                expect(page.locator('#censorly-word-report-success')).to_be_visible()
                request = page.evaluate('window.__reportRequest')
                self.assertEqual(request['url'], '/censorly/word-report/')
                self.assertEqual(request['options']['method'], 'POST')
                self.assertEqual(request['options']['headers']['X-Interoves-Game'], 'censorly')
                self.assertEqual(request['options']['headers']['X-Interoves-Gameplay-Context'], 'ctx-test')
                self.assertIn('FALSE_POSITIVE', request['options']['body'])
                self.assertIn('Поддержка', request['options']['body'])
                self.assertIn('Держать', request['options']['body'])
            finally:
                browser.close()
