"""Check that a guess redraws only the touched runs, matching a full render."""

import json
import os
from pathlib import Path
from unittest import SkipTest, skipUnless

from django.test import SimpleTestCase

from games.censorly.redact import build_public_view
from games.censorly.tokenize import (
    HEADING_END, HEADING_LEVEL_SEP, HEADING_START, build_puzzle_payload, title_content_lemmas,
)
from games.views.censorly_views import _compact_guess_response


ROOT = Path(__file__).resolve().parents[2]
ORIGIN = 'http://censorly.test'

BODY = (
    'Кот сидит у окна, «зверь» глядит на улицу — (тихо) и долго.\n\n'
    + HEADING_START + '2' + HEADING_LEVEL_SEP + 'Зверь и люди' + HEADING_END
    + '\nКот-воркот любит людей, улицу и окна.'
)

PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<main id="censorly-root" class="censorly" data-guess-url="/guess">
<div id="censorly-title"></div>
<div id="censorly-body" class="censorly__body"></div>
<form id="censorly-form"><input id="censorly-word"><button id="censorly-submit">Go</button></form>
<div id="censorly-feedback" hidden></div>
<table><tbody id="censorly-guess-list"></tbody></table>
</main>
<script id="censorly-bootstrap" type="application/json">%s</script>
<script src="/censorly_punctuation.js"></script>
<script src="/censorly_play.js"></script>
</body></html>'''

# Classes and attributes that legitimately differ from a fresh render:
# flash/selection follow the last action, length overrides are per player.
NORMALIZED_HTML = '''selector => {
    const clone = document.querySelector(selector).cloneNode(true);
    clone.querySelectorAll('*').forEach(node => {
        node.classList.remove('censorly-tok--just', 'censorly-tok--selected');
        node.removeAttribute('data-len-forced');
        if (node.getAttribute('class') === '') node.removeAttribute('class');
    });
    return clone.innerHTML;
}'''


def _view(payload, lemmas, guesses):
    state = build_public_view(payload, revealed_lemmas=lemmas, last_lemma=lemmas[-1] if lemmas else None)
    state['guesses'] = guesses
    return state


@skipUnless(os.environ.get('INTEROVES_LIVE_BROWSER_TESTS') == '1', 'Requires Chromium')
class CensorlyPatchBrowserTests(SimpleTestCase):
    def test_guess_patches_only_touched_runs(self):
        from playwright.sync_api import sync_playwright

        payload = build_puzzle_payload(wiki_title='Кот', body_text=BODY)
        title_lemmas = title_content_lemmas(payload)
        lemmas = []
        for tok in payload['body_tokens']:
            lemma = tok.get('lemma')
            if tok.get('kind') == 'content' and lemma and lemma not in title_lemmas and lemma not in lemmas:
                lemmas.append(lemma)
        self.assertGreaterEqual(len(lemmas), 4)

        responses = []
        guesses = []
        previous = _view(payload, [], [])
        for count in range(1, len(lemmas) + 1):
            current = _view(payload, lemmas[:count], [])
            before = {t['id']: t.get('revealed') for t in previous['title_tokens'] + previous['body_tokens']}
            newly = [
                t['id'] for t in current['title_tokens'] + current['body_tokens']
                if t.get('revealed') and not before.get(t['id'])
            ]
            guesses = guesses + [{'word': lemmas[count - 1], 'lemma': lemmas[count - 1],
                                  'hits': len(newly), 'token_ids': newly}]
            current['guesses'] = guesses
            result = dict(current, status='hit', hits=len(newly), newly_revealed=newly)
            responses.append((_compact_guess_response(result), current))
            previous = current

        pages = {'/play': PAGE % json.dumps(_view(payload, [], []))}

        def handle(route):
            path = route.request.url[len(ORIGIN):]
            if path == '/guess':
                route.fulfill(content_type='application/json', body=json.dumps(handle.next_response))
            elif path.endswith('.js'):
                route.fulfill(path=str(ROOT / 'static/js' / path.lstrip('/')))
            else:
                route.fulfill(content_type='text/html', body=pages[path])

        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                raise SkipTest('Playwright Chromium is not installed')
            browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
            try:
                page = browser.new_page()
                reference = browser.new_page()
                for p in (page, reference):
                    p.route(ORIGIN + '/**', handle)
                page.goto(ORIGIN + '/play')
                for step, (response, full_state) in enumerate(responses, start=1):
                    self.assertIn('state_delta', response)
                    # Mark current nodes and force lengths on every mask:
                    # untouched nodes must survive, patched runs must keep the flag.
                    page.evaluate('''() => {
                        document.querySelectorAll('#censorly-body > *').forEach(n => { n._kept = true; });
                        document.querySelectorAll('.censorly-tok--mask').forEach(n => { n.dataset.lenForced = '1'; });
                    }''')
                    handle.next_response = response
                    page.fill('#censorly-word', full_state['guesses'][-1]['word'])
                    page.press('#censorly-word', 'Enter')
                    page.wait_for_function(
                        'n => document.querySelectorAll("#censorly-guess-list tr").length === n', arg=step,
                    )
                    pages['/reference'] = PAGE % json.dumps(full_state)
                    reference.goto(ORIGIN + '/reference')
                    with self.subTest(step=step):
                        for selector in ('#censorly-title', '#censorly-body'):
                            self.assertEqual(
                                page.evaluate(NORMALIZED_HTML, selector),
                                reference.evaluate(NORMALIZED_HTML, selector),
                            )
                        kept, total = page.evaluate('''() => {
                            const nodes = Array.from(document.querySelectorAll('#censorly-body > *'));
                            return [nodes.filter(n => n._kept).length, nodes.length];
                        }''')
                        self.assertGreater(kept, total // 2)
                        self.assertLess(kept, total)
                        self.assertFalse(page.evaluate('''() =>
                            Array.from(document.querySelectorAll('.censorly-tok--mask'))
                                .some(n => n.dataset.lenForced !== '1')
                        '''))
                        self.assertEqual(
                            page.locator('#censorly-guess-list tr').first.get_attribute('data-guess-index'),
                            str(step - 1),
                        )
            finally:
                browser.close()
