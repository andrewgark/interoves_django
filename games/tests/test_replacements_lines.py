from django.test import SimpleTestCase

from games.check import ReplacementsLinesChecker
from games.util import clean_text
import json

from games.replacements_lines import (
    canonical_replacements_checker_line,
    parse_replacements_checker_json_lines,
    parse_replacements_lines_text,
    replacements_strip_literal_numeric_underscores,
    split_slot_answer_alternatives,
)


class SplitSlotAnswerAlternativesTests(SimpleTestCase):
    def test_single(self):
        self.assertEqual(split_slot_answer_alternatives('  КОТ  '), ('КОТ', ['КОТ']))

    def test_pipe_alternatives(self):
        c, opts = split_slot_answer_alternatives('КОТ|котик|КОТИК')
        self.assertEqual(c, 'КОТ')
        self.assertEqual(opts, ['КОТ', 'котик', 'КОТИК'])


class CanonicalReplacementsCheckerLineTests(SimpleTestCase):
    def test_strips_alternatives_in_underscore_slot(self):
        self.assertEqual(
            canonical_replacements_checker_line('был _КОТ|котик_ тут'),
            'был КОТ тут',
        )


class ParseReplacementsLinesTextTests(SimpleTestCase):
    def test_json_checker_lines_not_parsed_as_caps_from_whole_blob(self):
        """JSON checker_data не должен прогоняться через _segments_and_slot_values целиком."""
        left = (
            'FAR _23_ - ЖЕЛЕЗНАЯ ПРОМ-ЗОНА из ЕВРОДЫ\n'
            'SUM _41_ - ИЗВЕСТНАЯ РОК-ГРУППА из КАНАДЫ'
        )
        checker = json.dumps(
            {
                'lines': [
                    ['a', 'b', 'c', 'd', 'e', 'f'],
                    ['x', 'y', 'z', 'w', 'v', 'u'],
                ]
            },
            ensure_ascii=False,
        )
        p = parse_replacements_lines_text(left, checker)
        self.assertEqual(p['answers'][0][0], 'a')
        self.assertEqual(p['answers'][1][0], 'x')
        self.assertNotIn('SUM', p['answers'][0])

    def test_parse_replacements_checker_json_lines_matches_checker(self):
        raw = json.dumps({'lines': [['КОТ|кот']]})
        pr = parse_replacements_checker_json_lines(raw)
        self.assertIsNotNone(pr)
        canon, accept = pr
        self.assertEqual(canon[0][0], 'КОТ')
        self.assertEqual(accept[0][0], ['КОТ', 'кот'])

    def test_answer_accept_parallel_to_answers(self):
        left = '_X_ _Y_'
        ans = '_A|a_ _B_'
        p = parse_replacements_lines_text(left, ans)
        self.assertEqual(p['answers'], [['A', 'B']])
        self.assertEqual(p['answer_accept'], [[['A', 'a'], ['B']]])

    def test_far_numeric_underscore_is_second_slot(self):
        left = 'FAR _23_ - ЖЕЛЕЗНАЯ'
        p = parse_replacements_lines_text(left, '')
        self.assertEqual(len(p['answers'][0]), 3)
        self.assertEqual(p['answers'][0][0], 'FAR')
        self.assertEqual(p['answers'][0][1], '23')
        self.assertEqual(p['answers'][0][2], 'ЖЕЛЕЗНАЯ')

    def test_numeric_underscore_is_separate_slot_left_still_pretty(self):
        left = 'ПРАВО на СВЯЩЁНА было по _76_ серии.'
        p = parse_replacements_lines_text(left, '')
        self.assertEqual(
            p['left_lines'][0],
            'ПРАВО на СВЯЩЁНА было по 76 серии.',
        )
        # два капс-слота + _76_ как отдельный слот
        self.assertEqual(len(p['answers'][0]), 3)
        self.assertEqual(p['answers'][0][2], '76')


class CapsSlotUnicodeTests(SimpleTestCase):
    def test_taifoen_one_slot_with_latin_e_diaeresis(self):
        left = 'TAIFOËN - это ТЕЛЕВИЗОР.'
        p = parse_replacements_lines_text(left, '')
        self.assertEqual(len(p['answers'][0]), 2)
        self.assertEqual(p['answers'][0][0], 'TAIFOËN')
        self.assertEqual(p['answers'][0][1], 'ТЕЛЕВИЗОР')


class StripLiteralNumericUnderscoresTests(SimpleTestCase):
    def test_strip(self):
        self.assertEqual(
            replacements_strip_literal_numeric_underscores('по _76_ серии'),
            'по 76 серии',
        )

    def test_underscore_word_slot_unchanged(self):
        self.assertEqual(
            replacements_strip_literal_numeric_underscores('_КОТ_'),
            '_КОТ_',
        )


class ReplacementsLinesCheckerTests(SimpleTestCase):
    def _attempt(self, task_text, checker_data, last_state=None):
        task = type('Task', (), {'text': task_text, 'checker_data': checker_data})()
        return type('Attempt', (), {'task': task})()

    def test_accepts_alternative_spelling(self):
        task_text = '_СЛОВО_'
        checker_data = '_СЛОВО|опция1|опция2_'
        ch = ReplacementsLinesChecker('', None)
        att = self._attempt(task_text, checker_data)
        payload = '{"line_index": 0, "answers": ["опция1"]}'
        r = ch.check(payload, att)
        self.assertEqual(r.status, 'Ok')

    def test_rejects_wrong_not_in_list(self):
        task_text = '_СЛОВО_'
        checker_data = '_СЛОВО|опция1_'
        ch = ReplacementsLinesChecker('', None)
        att = self._attempt(task_text, checker_data)
        payload = '{"line_index": 0, "answers": ["другое"]}'
        r = ch.check(payload, att)
        self.assertNotEqual(r.status, 'Ok')

    def test_accepts_citroen_caps_slot_exact(self):
        task_text = 'CITROËN'
        checker_data = 'CITROËN'
        ch = ReplacementsLinesChecker('', None)
        att = self._attempt(task_text, checker_data)
        payload = '{"line_index": 0, "answers": ["CITROËN"]}'
        r = ch.check(payload, att)
        self.assertEqual(r.status, 'Ok')

    def test_accepts_citroen_caps_slot_case_insensitive(self):
        task_text = 'CITROËN'
        checker_data = 'CITROËN'
        ch = ReplacementsLinesChecker('', None)
        att = self._attempt(task_text, checker_data)
        payload = '{"line_index": 0, "answers": ["citroën"]}'
        r = ch.check(payload, att)
        self.assertEqual(r.status, 'Ok')

    def test_accepts_citroen_underscore_slot(self):
        task_text = '_МАРКА_'
        checker_data = '_CITROËN_'
        ch = ReplacementsLinesChecker('', None)
        att = self._attempt(task_text, checker_data)
        payload = '{"line_index": 0, "answers": ["CITROËN"]}'
        r = ch.check(payload, att)
        self.assertEqual(r.status, 'Ok')

    def test_tournament_ok_when_line_correct_but_task_not_complete(self):
        """Полностью верная строка не должна давать tournament_status Pending в турнире."""
        checker_data = json.dumps({'lines': [['a'], ['b']]})
        ch = ReplacementsLinesChecker(checker_data, None)
        att = self._attempt('', '')
        r = ch.check('{"line_index": 0, "answers": ["a"]}', att)
        self.assertEqual(r.status, 'Partial')
        self.assertEqual(r.tournament_status, 'Ok')

    def test_tournament_pending_when_line_wrong_but_task_not_complete(self):
        checker_data = json.dumps({'lines': [['a'], ['b']]})
        ch = ReplacementsLinesChecker(checker_data, None)
        att = self._attempt('', '')
        r = ch.check('{"line_index": 0, "answers": ["wrong"]}', att)
        self.assertNotEqual(r.status, 'Ok')
        self.assertEqual(r.tournament_status, 'Pending')

    def test_tournament_ok_when_fewer_answers_than_slots(self):
        checker_data = json.dumps({'lines': [['a', 'b'], ['x']]})
        ch = ReplacementsLinesChecker(checker_data, None)
        att = self._attempt('', '')
        r = ch.check('{"line_index": 0, "answers": ["a"]}', att)
        self.assertNotEqual(r.status, 'Ok')
        self.assertEqual(r.tournament_status, 'Ok')

    def test_tournament_ok_when_empty_cell_in_line(self):
        checker_data = json.dumps({'lines': [['a', 'b'], ['x']]})
        ch = ReplacementsLinesChecker(checker_data, None)
        att = self._attempt('', '')
        r = ch.check('{"line_index": 0, "answers": ["a", ""]}', att)
        self.assertNotEqual(r.status, 'Ok')
        self.assertEqual(r.tournament_status, 'Ok')

    def test_tournament_ok_when_whitespace_only_cell(self):
        checker_data = json.dumps({'lines': [['a', 'b'], ['x']]})
        ch = ReplacementsLinesChecker(checker_data, None)
        att = self._attempt('', '')
        r = ch.check('{"line_index": 0, "answers": ["a", "   "]}', att)
        self.assertNotEqual(r.status, 'Ok')
        self.assertEqual(r.tournament_status, 'Ok')


class CleanTextLatinDiaeresisTests(SimpleTestCase):
    def test_citroen_lowercase_stable_for_comparison(self):
        self.assertEqual(clean_text('CITROËN'), clean_text('citroën'))
