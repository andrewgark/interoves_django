"""Check that guesses, hints and syncs redraw only the touched runs.

Every patched article must match a full render of the same state.
"""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest import SkipTest, skipUnless

from django.test import SimpleTestCase

from games.puzzles.censorly.article.redact import build_public_view
from games.puzzles.censorly.text.tokenize import (
    HEADING_END, HEADING_LEVEL_SEP, HEADING_START, build_puzzle_payload, title_content_lemmas,
)
from games.site.views.daily.censorly import _compact_play_response


ROOT = Path(__file__).resolve().parents[2]
ORIGIN = 'http://censorly.test'

BODY = (
    'Кот сидит у окна, «зверь» глядит на улицу — (тихо) и долго.\n\n'
    + HEADING_START + '2' + HEADING_LEVEL_SEP + 'Зверь и люди' + HEADING_END
    + '\nКот-воркот любит людей, улицу и окна.'
)

PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<main id="censorly-root" class="censorly" data-guess-url="/guess" data-hint-url="/hint"%s>
<div id="censorly-title"></div>
<div id="censorly-body" class="censorly__body"></div>
<form id="censorly-form"><input id="censorly-word"><button id="censorly-submit">Go</button></form>
<button id="censorly-hint-btn" type="button">Hint</button>
<div id="censorly-hint-mode" hidden></div>
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

MARK_NODES = '''() => {
    document.querySelectorAll('#censorly-body > *').forEach(n => { n._kept = true; });
    document.querySelectorAll('.censorly-tok--mask').forEach(n => { n.dataset.lenForced = '1'; });
}'''

KEPT_NODES = '''() => {
    const nodes = Array.from(document.querySelectorAll('#censorly-body > *'));
    return [nodes.filter(n => n._kept).length, nodes.length];
}'''

LEN_FORCED_LOST = '''() => Array.from(document.querySelectorAll('.censorly-tok--mask'))
    .some(n => n.dataset.lenForced !== '1')'''


def _page(state, state_url=False):
    return PAGE % (' data-state-url="/state"' if state_url else '', json.dumps(state))


class _Article:
    """Public states of one puzzle as the player opens its non-title words."""

    def __init__(self):
        self.payload = build_puzzle_payload(wiki_title='Кот', body_text=BODY)
        title_lemmas = title_content_lemmas(self.payload)
        self.lemmas = []
        for tok in self.payload['body_tokens']:
            lemma = tok.get('lemma')
            if tok.get('kind') == 'content' and lemma and lemma not in title_lemmas \
                    and lemma not in self.lemmas:
                self.lemmas.append(lemma)
        self.guesses = []
        self.hinted = []
        self.state = self.view([])

    def view(self, opened):
        state = build_public_view(
            self.payload, revealed_lemmas=opened, hinted_lemmas=self.hinted,
            last_lemma=opened[-1] if opened else None,
        )
        state['guesses'] = list(self.guesses)
        state['hinted_lemmas'] = list(self.hinted)
        return state

    def open(self, lemma, *, hint=False):
        """Open lemma; return (full state, newly revealed ids)."""
        opened = [g['lemma'] for g in self.guesses] + self.hinted + [lemma]
        if hint:
            self.hinted.append(lemma)
        current = self.view(opened)
        before = {t['id']: t.get('revealed') for t in self.state['title_tokens'] + self.state['body_tokens']}
        newly = [
            t['id'] for t in current['title_tokens'] + current['body_tokens']
            if t.get('revealed') and not before.get(t['id'])
        ]
        if not hint:
            self.guesses.append({'word': lemma, 'lemma': lemma, 'hits': len(newly), 'token_ids': newly})
            current['guesses'] = list(self.guesses)
        self.state = current
        return current, newly


@skipUnless(os.environ.get('INTEROVES_LIVE_BROWSER_TESTS') == '1', 'Requires Chromium')
class CensorlyPatchBrowserTests(SimpleTestCase):
    @contextmanager
    def _browser(self, pages, responses):
        from playwright.sync_api import sync_playwright

        def handle(route):
            path = route.request.url[len(ORIGIN):]
            if path in responses:
                route.fulfill(content_type='application/json', body=json.dumps(responses[path]))
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
                yield page, reference
            finally:
                browser.close()

    def _assert_matches_full_render(self, page, reference, pages, full_state):
        pages['/reference'] = _page(full_state)
        reference.goto(ORIGIN + '/reference')
        for selector in ('#censorly-title', '#censorly-body'):
            self.assertEqual(
                page.evaluate(NORMALIZED_HTML, selector),
                reference.evaluate(NORMALIZED_HTML, selector),
            )
        kept, total = page.evaluate(KEPT_NODES)
        self.assertGreater(kept, total // 2)
        self.assertLess(kept, total)
        self.assertFalse(page.evaluate(LEN_FORCED_LOST))

    def test_guess_patches_only_touched_runs(self):
        article = _Article()
        self.assertGreaterEqual(len(article.lemmas), 4)
        pages = {'/play': _page(article.state)}
        responses = {}
        with self._browser(pages, responses) as (page, reference):
            page.goto(ORIGIN + '/play')
            for step, lemma in enumerate(article.lemmas, start=1):
                full_state, newly = article.open(lemma)
                result = dict(full_state, status='hit', hits=len(newly), newly_revealed=newly)
                responses['/guess'] = _compact_play_response(result)
                self.assertIn('state_delta', responses['/guess'])
                page.evaluate(MARK_NODES)
                page.fill('#censorly-word', lemma)
                page.press('#censorly-word', 'Enter')
                page.wait_for_function(
                    'n => document.querySelectorAll("#censorly-guess-list tr").length === n', arg=step,
                )
                with self.subTest(step=step):
                    self._assert_matches_full_render(page, reference, pages, full_state)
                    self.assertEqual(
                        page.locator('#censorly-guess-list tr').first.get_attribute('data-guess-index'),
                        str(step - 1),
                    )

    def test_hint_patches_only_touched_runs(self):
        article = _Article()
        pages = {'/play': _page(article.state)}
        responses = {}
        with self._browser(pages, responses) as (page, reference):
            page.goto(ORIGIN + '/play')
            # Masked public tokens hide their lemma; look it up in the payload.
            target = next(t for t in article.payload['body_tokens'] if t.get('lemma') == article.lemmas[0])
            full_state, newly = article.open(article.lemmas[0], hint=True)
            result = dict(full_state, status='hint', newly_revealed=newly, hint_lemma=article.lemmas[0])
            responses['/hint'] = _compact_play_response(result)
            self.assertIn('state_delta', responses['/hint'])
            page.evaluate(MARK_NODES)
            page.click('#censorly-hint-btn')
            page.click('.censorly-tok--mask[data-id="%s"]' % target['id'])
            page.wait_for_function(
                'id => document.querySelector(`.censorly-tok--hinted[data-id="${id}"]`)', arg=target['id'],
            )
            self._assert_matches_full_render(page, reference, pages, full_state)

    def test_synced_state_patches_only_changed_tokens(self):
        article = _Article()
        article.open(article.lemmas[0])
        pages = {'/play': _page(article.state, state_url=True)}
        synced_state, _ = article.open(article.lemmas[1])
        responses = {'/state': synced_state}
        with self._browser(pages, responses) as (page, reference):
            # Mark the bootstrap render before the boot-time sync replaces it.
            page.add_init_script('''
                document.addEventListener('DOMContentLoaded', () => {
                    const fetch = window.fetch;
                    window.fetch = (...args) => new Promise(resolve => setTimeout(resolve, 0))
                        .then(() => { (%s)(); return fetch(...args); });
                });
            ''' % MARK_NODES)
            page.goto(ORIGIN + '/play')
            page.wait_for_function('() => document.querySelectorAll("#censorly-guess-list tr").length === 2')
            self._assert_matches_full_render(page, reference, pages, synced_state)
