"""Censorly structures and responses stay compact without changing meaning."""

import json
from pathlib import Path
from unittest import TestCase

from games.puzzles.censorly.text import roots
from games.puzzles.censorly.lexical.match import rootbank
from games.puzzles.censorly.lexical.match.core import WORD_CACHE_SIZE
from games.puzzles.censorly.lexical import semantics
from games.puzzles.censorly.lexical.russian import context, morphology
from games.puzzles.censorly.article.redact import build_public_view
from games.puzzles.censorly.text.tokenize import build_puzzle_payload
from games.site.views.daily.censorly import _article_json

_ARTICLE = Path(__file__).with_name('censorly_testdata') / 'articles' / 'saturn.txt'


class PublicTokenFlagsTests(TestCase):
    def setUp(self):
        self.payload = build_puzzle_payload(
            wiki_title='Сатурн', body_text=_ARTICLE.read_text(encoding='utf-8'),
        )

    def test_false_flags_are_omitted_and_true_flags_kept(self):
        content = next(
            tok for tok in self.payload['body_tokens']
            if tok.get('kind') == 'content' and tok.get('lemma')
        )
        lemma = content['lemma']

        masked = build_public_view(self.payload)
        opened = build_public_view(
            self.payload, revealed_lemmas={lemma}, hinted_lemmas={lemma}, last_lemma=lemma,
        )

        masked_tok = next(t for t in masked['body_tokens'] if t['id'] == content['id'])
        opened_tok = next(t for t in opened['body_tokens'] if t['id'] == content['id'])
        self.assertFalse(masked_tok['revealed'])
        for flag in ('in_title', 'guessed', 'hinted', 'just_revealed'):
            self.assertNotIn(flag, masked_tok)
        self.assertTrue(opened_tok['revealed'])
        self.assertTrue(opened_tok['guessed'])
        self.assertTrue(opened_tok['hinted'])
        self.assertTrue(opened_tok['just_revealed'])

    def test_structural_tokens_stay_revealed_and_title_tokens_marked(self):
        view = build_public_view(self.payload)

        spaces = [t for t in view['body_tokens'] if t['kind'] == 'space']
        self.assertTrue(spaces)
        self.assertTrue(all(t['revealed'] and 'in_title' not in t for t in spaces))
        self.assertTrue(all(t.get('in_title') is True for t in view['title_tokens']))


class ArticleJsonTests(TestCase):
    def test_article_json_is_compact_utf8_and_round_trips(self):
        payload = {'status': 'ok', 'body_tokens': [{'id': 1, 'text': 'Сатурн'}]}

        response = _article_json(payload, status=400)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.content,
            '{"status":"ok","body_tokens":[{"id":1,"text":"Сатурн"}]}'.encode(),
        )
        self.assertEqual(json.loads(response.content), payload)


class LexicalCacheBoundsTests(TestCase):
    def test_per_surface_caches_share_one_bounded_size(self):
        caches = (
            semantics.readings_of,
            semantics.guess_readings_of,
            semantics.preferred_readings_of,
            semantics._proper_pair,
            context.guess_inflection_ids,
            context.bare_target,
            context._ungated,
            morphology.pos_of,
            morphology._readings,
        )
        self.assertLessEqual(WORD_CACHE_SIZE, 65_536)
        for cache in caches:
            self.assertEqual(cache.cache_info().maxsize, WORD_CACHE_SIZE, cache.__name__)


class SharedStructureTests(TestCase):
    def test_rootbank_shares_equal_structures_and_root_ids(self):
        bank, _seconds = rootbank._bank()
        by_value = {}
        for structures in bank.values():
            self.assertIs(by_value.setdefault(structures, structures), structures)
        ids = {}
        for structures in list(by_value)[:2000]:
            for structure in structures:
                for root_id in structure:
                    self.assertIs(ids.setdefault(root_id, root_id), root_id)

    def test_gameplay_bank_matches_diagnostic_build(self):
        bank, _seconds = rootbank._bank()
        provenance = rootbank.provenance()

        self.assertTrue(provenance)
        self.assertEqual(rootbank.assignment_of('красивый')['structures'],
                         rootbank.structures_of('красивый'))
        rebuilt, _seconds, _kuz, tikhonov, _prov, _audit = rootbank._build_bank(record=True)
        self.assertEqual(rebuilt, bank)
        self.assertTrue(tikhonov)
        self.assertEqual(rootbank.tikhonov_roots_of('водопад'), tikhonov.get('водопад', ()))

    def test_cognate_index_shares_equal_variant_lists(self):
        index, segments = roots._load_index()
        by_value = {}
        for variants in index.values():
            self.assertIs(by_value.setdefault(variants, variants), variants)
        by_segments = {}
        for parts in segments.values():
            self.assertIs(by_segments.setdefault(parts, parts), parts)
