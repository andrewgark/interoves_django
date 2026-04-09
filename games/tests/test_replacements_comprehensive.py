# -*- coding: utf-8 -*-
"""
Интеграционные и регрессионные тесты для парсера «Замены» (games.replacements_lines).

Инварианты, которые фиксируем:
- Слоты: капс-последовательности (2+ Lu) и пары _…_; внутри _…_ символ | — только альтернативы ответа.
- Левый столбец и правый разбираются одним и тем же _find_slots_in_order; число слотов в строке условия
  должно совпадать с числом ячеек в checker_data (текст или JSON), иначе ответы «съезжают».
- #…# — литерал капса, не слот; не путать с | внутри _A|B_.
"""
import json

from django.test import SimpleTestCase

from games.check import ReplacementsLinesChecker
from games.replacements_lines import (
    canonical_replacements_checker_line,
    parse_replacements_lines_text,
)


def _n_slots(line):
    return len(parse_replacements_lines_text(line, '')['answers'][0])


class UnderscoreSlotAndAlternativesTests(SimpleTestCase):
    """_СЛОТ_ и _КАНОН|альт1|альт2_ — один слот, | внутри — не разделитель слотов."""

    def test_one_underscore_slot_three_alternatives(self):
        left = '_FOO_'
        p = parse_replacements_lines_text(left, '_FOO|foo|f_')
        self.assertEqual(p['answers'][0], ['FOO'])
        self.assertEqual(p['answer_accept'][0][0], ['FOO', 'foo', 'f'])

    def test_two_underscore_slots_with_alternatives(self):
        left = '_A_ _B_'
        p = parse_replacements_lines_text(left, '_A|a_ _B|b_')
        self.assertEqual(p['answers'], [['A', 'B']])
        self.assertEqual(p['answer_accept'], [[['A', 'a'], ['B', 'b']]])

    def test_caps_inside_underscore_not_second_caps_slot(self):
        """Капс внутри _…_ не дублируется как отдельный капс-слот."""
        left = '_HELLO_'
        p = parse_replacements_lines_text(left, '')
        self.assertEqual(_n_slots(left), 1)
        self.assertEqual(p['answers'][0][0], 'HELLO')

    def test_underscore_then_caps_two_slots(self):
        left = '_X_ WORD'
        p = parse_replacements_lines_text(left, '_a_ BB')
        self.assertEqual(p['answers'][0], ['a', 'BB'])

    def test_canonical_line_strips_underscores_and_first_alternative_only(self):
        self.assertEqual(
            canonical_replacements_checker_line('prefix _A|a|aa_ suffix'),
            'prefix A suffix',
        )

    def test_cyrillic_underscore_alternatives(self):
        p = parse_replacements_lines_text(
            '_СЛОВО_ ДРУГОЕ',
            '_ОТВЕТ|ответик|ОТВЕТИК_ ЗНАЧЕНИЕ',
        )
        self.assertEqual(p['answers'][0], ['ОТВЕТ', 'ЗНАЧЕНИЕ'])
        self.assertEqual(
            p['answer_accept'][0][0],
            ['ОТВЕТ', 'ответик', 'ОТВЕТИК'],
        )


class LeftCheckerSlotCountAlignmentTests(SimpleTestCase):
    """
    Регресс: «написал _С1|С2|С3_ вместо отдельных капс-слов» — если слева два слота, а справа один _…_,
    число слотов не совпадает и ответы привязываются к чужим слотам.
    """

    def test_two_caps_left_one_underscore_checker_collapses_to_one_slot(self):
        left = 'ALPHA BETA'
        checker = '_ALPHA|alpha|a_'
        p = parse_replacements_lines_text(left, checker)
        self.assertEqual(_n_slots(left), 2)
        self.assertEqual(_n_slots(checker), 1)
        self.assertEqual(len(p['answers'][0]), 2)
        # второй слот подтянут из подсказки слева (BETA), а не из checker
        self.assertEqual(p['answers'][0][0], 'ALPHA')
        self.assertEqual(p['answers'][0][1], 'BETA')

    def test_two_caps_left_two_slots_second_is_underscore_alternatives_ok(self):
        left = 'ALPHA BETA'
        checker = 'ALPHA _B|b|bb_'
        p = parse_replacements_lines_text(left, checker)
        self.assertEqual(p['answers'][0], ['ALPHA', 'B'])
        self.assertEqual(p['answer_accept'][0][1], ['B', 'b', 'bb'])

    def test_three_caps_left_checker_has_two_slots_misaligned(self):
        left = 'AA BB CC'
        checker = '_A|a_ BB'
        p = parse_replacements_lines_text(left, checker)
        self.assertEqual(len(p['answers'][0]), 3)
        self.assertEqual(p['answers'][0][2], 'CC')  # из левой подсказки

    def test_json_overrides_misaligned_plain_checker(self):
        left = 'AA BB CC'
        checker_plain = '_X_ Y'
        bad = parse_replacements_lines_text(left, checker_plain)
        self.assertNotEqual(bad['answers'][0], ['a', 'b', 'c'])
        fixed = json.dumps({'lines': [['a', 'b', 'c']]})
        good = parse_replacements_lines_text(left, fixed)
        self.assertEqual(good['answers'][0], ['a', 'b', 'c'])


class MixedCapsUnderscoreHashLiteralTests(SimpleTestCase):
    def test_hash_literal_does_not_split_underscore_alternatives(self):
        line = '_A|B_ #DC# WORD'
        p = parse_replacements_lines_text(line, '')
        self.assertEqual(_n_slots(line), 2)
        self.assertEqual(p['left_lines'][0], '_A|B_ DC WORD')

    def test_underscore_slot_preserves_pipe_chars_in_content(self):
        """Содержимое слота 'A|B' — одна строка до split_slot_answer_alternatives."""
        p = parse_replacements_lines_text('_A|B_', '_X|Y|Z_')
        self.assertEqual(p['answer_accept'][0][0], ['X', 'Y', 'Z'])


class LatinEnglishCapsTests(SimpleTestCase):
    """Латиница в слотах (игры с английскими словами в строке)."""

    def test_english_all_caps_words_are_slots(self):
        left = 'THE QUICK BROWN FOX'
        p = parse_replacements_lines_text(left, '')
        self.assertEqual(p['answers'][0], ['THE', 'QUICK', 'BROWN', 'FOX'])

    def test_mixed_cyrillic_and_english_caps(self):
        left = 'HELLO ПРИВЕТ WORLD'
        p = parse_replacements_lines_text(left, '')
        self.assertEqual(p['answers'][0], ['HELLO', 'ПРИВЕТ', 'WORLD'])

    def test_checker_plain_english_matches_left_slot_count(self):
        left = 'CAT DOG'
        checker = 'DOG CAT'
        p = parse_replacements_lines_text(left, checker)
        self.assertEqual(p['answers'][0], ['DOG', 'CAT'])

    def test_iphone_not_split_as_slot(self):
        """Граница слота: не буква Lu/Ll/... — «iPhone» не даёт слот IPHONE."""
        left = 'iPhone CASE'
        p = parse_replacements_lines_text(left, '')
        self.assertEqual(p['answers'][0], ['CASE'])

    def test_replacements_checker_accepts_english_line(self):
        ch = ReplacementsLinesChecker('', None)
        task = type(
            'Task',
            (),
            {'text': 'ONE TWO', 'checker_data': 'THREE FOUR'},
        )()
        att = type('Attempt', (), {'task': task})()
        r = ch.check(
            json.dumps({'line_index': 0, 'answers': ['THREE', 'FOUR']}, ensure_ascii=False),
            att,
        )
        self.assertEqual(r.status, 'Ok')


class NumericUnderscoreEdgeTests(SimpleTestCase):
    def test_only_numeric_underscore_stripped_on_left_column(self):
        left = 'AA _42_ BB'
        p = parse_replacements_lines_text(left, '')
        self.assertEqual(p['left_lines'][0], 'AA 42 BB')
        self.assertEqual(p['answers'][0][1], '42')

    def test_underscore_with_digits_and_pipes_alternatives_in_one_slot(self):
        left = '_12|34_'
        p = parse_replacements_lines_text(left, '')
        self.assertEqual(p['answers'][0][0], '12')
        self.assertEqual(p['answer_accept'][0][0], ['12', '34'])


class ReplacementsLinesCheckerUnderscoreIntegrationTests(SimpleTestCase):
    def test_checker_accepts_second_alternative(self):
        ch = ReplacementsLinesChecker('', None)
        task = type('Task', (), {'text': '_A_', 'checker_data': '_A|altA_'})()
        att = type('Attempt', (), {'task': task})()
        r = ch.check(
            json.dumps({'line_index': 0, 'answers': ['altA']}, ensure_ascii=False),
            att,
        )
        self.assertEqual(r.status, 'Ok')

    def test_multi_line_json_first_row_underscore_alts(self):
        data = json.dumps({'lines': [['X|x', 'Y'], ['Z']]})
        ch = ReplacementsLinesChecker(data, None)
        task = type('Task', (), {'text': '_A_ B\nC', 'checker_data': data})()
        att = type('Attempt', (), {'task': task})()
        r0 = ch.check(
            json.dumps({'line_index': 0, 'answers': ['x', 'Y']}, ensure_ascii=False),
            att,
        )
        self.assertEqual(r0.tournament_status, 'Ok')
