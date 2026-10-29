"""Shared normalization contract: games/puzzles/core/normalize_ops.py ↔ static/js/offline_normalize.js."""
import json
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from games.puzzles.core.check_runtime import EqualsWithPossibleSpacesChecker, HangmanLettersChecker
from games.puzzles.core.normalize_ops import (
    CLEAN,
    CLEAN_KEEP_YO,
    OPS,
    RADDLE_WORD,
    normalize,
    normalized,
    validate_ops,
)
from games.puzzles.raddle.engine import raddle_word_core
from games.results.util import clean_text

BASE_DIR = Path(settings.BASE_DIR)
CONTRACT = BASE_DIR / 'contracts' / 'normalize.json'
JS_TWIN = BASE_DIR / 'static' / 'js' / 'offline_normalize.js'


def _contract_cases():
    with CONTRACT.open(encoding='utf-8') as f:
        return json.load(f)['cases']


class NormalizeContractTests(SimpleTestCase):
    def test_python_reproduces_contract(self):
        for case in _contract_cases():
            with self.subTest(ops=case['ops'], text=case['in']):
                self.assertEqual(normalize(case['in'], case['ops']), case['out'])

    def test_every_step_is_covered_by_contract(self):
        used = {op for case in _contract_cases() for op in case['ops']}
        self.assertEqual(used, set(OPS))

    def test_js_twin_defines_the_same_steps(self):
        source = JS_TWIN.read_text(encoding='utf-8')
        block = re.search(r'var OPS = \{(.*?)\n  \};', source, re.S).group(1)
        js_ops = set(re.findall(r'^    (\w+):', block, re.M))
        self.assertEqual(js_ops, set(OPS))


class NormalizeHelpersTests(SimpleTestCase):
    def test_clean_text_is_the_clean_pipeline(self):
        self.assertEqual(clean_text('  Ёлка '), normalize('  Ёлка ', CLEAN))
        self.assertEqual(clean_text('  Ёлка ', replace_ё=False), normalize('  Ёлка ', CLEAN_KEEP_YO))
        self.assertEqual(clean_text('  Ёлка '), 'елка')

    def test_raddle_word_core_is_the_raddle_pipeline(self):
        self.assertEqual(raddle_word_core('Пол-Литра!'), normalize('Пол-Литра!', RADDLE_WORD))
        self.assertEqual(raddle_word_core('Ёж'), 'еж')
        self.assertEqual(raddle_word_core(None), '')

    def test_unknown_step_is_rejected_at_decoration(self):
        with self.assertRaisesRegex(ValueError, 'nope'):
            validate_ops(('lower', 'nope'))
        with self.assertRaisesRegex(ValueError, 'nope'):
            normalized('nope')

    def test_decorated_methods_expose_their_pipeline(self):
        self.assertEqual(
            EqualsWithPossibleSpacesChecker.bool_check.normalize_ops,
            CLEAN + ('drop_inner_spaces', 'drop_punct'),
        )
        self.assertEqual(HangmanLettersChecker.check.normalize_ops, CLEAN_KEEP_YO + ('drop_punct',))
