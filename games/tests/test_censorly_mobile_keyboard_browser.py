"""Censorly on a touch phone: when the soft keyboard stays and how results show.

A found word closes the keyboard (the player wants to see what opened), a miss
keeps it for the next attempt, and any tap outside the input closes it. Short
results appear as a fading toast instead of growing the mobile dock.
"""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest import SkipTest, skipUnless

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]
ORIGIN = 'http://censorly.test'

# 16px input as in new.css: smaller fonts make mobile Chrome zoom on focus.
# The dock sits far from the text: touch adjustment snaps taps on plain text
# to a nearby control.
PAGE = '''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>#censorly-word { font-size: 16px; } .censorly__dock { margin-top: 300px; }</style></head><body>
<main id="censorly-root" class="censorly" data-guess-url="/guess">
<div id="censorly-title"></div>
<div id="censorly-body" class="censorly__body"></div>
<aside class="censorly__dock">
<form id="censorly-form"><input id="censorly-word"><button id="censorly-submit">Go</button></form>
<p id="censorly-feedback" hidden></p>
<table><tbody id="censorly-guess-list"></tbody></table>
</aside>
</main>
<script id="censorly-bootstrap" type="application/json">%s</script>
<script src="/censorly_punctuation.js"></script>
<script src="/censorly_play.js"></script>
</body></html>'''

WORDS = ['кот', 'сидит', 'дома']


def _state(opened, guesses):
    body = []
    for index, word in enumerate(WORDS):
        if index:
            body.append({'id': 100 + index, 'kind': 'space', 'text': ' '})
        token = {'id': index + 1, 'kind': 'content', 'lemma': word, 'length': len(word)}
        if word in opened:
            token.update(revealed=True, text=word)
        body.append(token)
    return {
        'title_tokens': [{'id': 0, 'kind': 'content', 'text': 'Статья', 'revealed': True}],
        'body_tokens': body,
        'guesses': guesses,
    }


ACTIVE_IS_INPUT = '() => document.activeElement === document.getElementById("censorly-word")'
TOAST_TEXT = '() => (document.querySelector(".censorly-toast") || {}).textContent || ""'


@skipUnless(os.environ.get('INTEROVES_LIVE_BROWSER_TESTS') == '1', 'Requires Chromium')
class CensorlyMobileKeyboardBrowserTests(SimpleTestCase):
    @contextmanager
    def _phone(self, responses):
        from playwright.sync_api import sync_playwright

        def handle(route):
            path = route.request.url[len(ORIGIN):]
            if path in responses:
                route.fulfill(content_type='application/json', body=json.dumps(responses[path]))
            elif path.endswith('.js'):
                route.fulfill(path=str(ROOT / 'static/js' / path.lstrip('/')))
            else:
                route.fulfill(content_type='text/html', body=PAGE % json.dumps(_state([], [])))

        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                raise SkipTest('Playwright Chromium is not installed')
            browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
            try:
                context = browser.new_context(
                    viewport={'width': 390, 'height': 760}, has_touch=True, is_mobile=True,
                )
                page = context.new_page()
                page.route(ORIGIN + '/**', handle)
                page.goto(ORIGIN + '/play')
                self.assertTrue(page.evaluate(
                    '() => matchMedia("(hover: none) and (pointer: coarse)").matches'
                ))
                yield page
            finally:
                browser.close()

    def _submit(self, page, word):
        page.tap('#censorly-word')
        page.fill('#censorly-word', word)
        page.tap('#censorly-submit')
        page.wait_for_function('() => !document.getElementById("censorly-submit").disabled')

    def test_found_word_closes_keyboard_and_miss_keeps_it(self):
        hit = {'word': 'сидит', 'lemma': 'сидит', 'hits': 1, 'token_ids': [2]}
        miss = {'word': 'лес', 'lemma': 'лес', 'hits': 0, 'token_ids': []}
        responses = {}
        with self._phone(responses) as page:
            responses['/guess'] = dict(
                _state(['сидит'], [hit]), status='hit', hits=1, newly_revealed=[2],
            )
            self._submit(page, 'сидит')
            self.assertFalse(page.evaluate(ACTIVE_IS_INPUT))
            self.assertIn('Есть совпадения', page.evaluate(TOAST_TEXT))
            self.assertTrue(page.locator('#censorly-feedback').is_hidden())

            responses['/guess'] = dict(
                _state(['сидит'], [hit, miss]), status='miss', hits=0, newly_revealed=[],
            )
            self._submit(page, 'лес')
            self.assertTrue(page.evaluate(ACTIVE_IS_INPUT))
            self.assertIn('Нет в тексте', page.evaluate(TOAST_TEXT))

            responses['/guess'] = dict(
                _state(['сидит'], [hit, miss]), status='duplicate', guess_word='лес',
                active_guess_index=1,
            )
            self._submit(page, 'лес')
            self.assertTrue(page.evaluate(ACTIVE_IS_INPUT))
            self.assertIn('уже вводили (#2)', page.evaluate(TOAST_TEXT))

    def test_tap_outside_input_closes_keyboard(self):
        miss = {'word': 'лес', 'lemma': 'лес', 'hits': 0, 'token_ids': []}
        responses = {'/guess': dict(_state([], [miss]), status='miss', hits=0, newly_revealed=[])}
        with self._phone(responses) as page:
            self._submit(page, 'лес')
            self.assertTrue(page.evaluate(ACTIVE_IS_INPUT))
            page.tap('#censorly-guess-list tr')
            self.assertFalse(page.evaluate(ACTIVE_IS_INPUT))

            page.tap('#censorly-word')
            self.assertTrue(page.evaluate(ACTIVE_IS_INPUT))
            page.tap('.censorly-tok--mask')
            self.assertFalse(page.evaluate(ACTIVE_IS_INPUT))
