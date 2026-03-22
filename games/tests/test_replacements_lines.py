from django.test import SimpleTestCase

from games.check import ReplacementsLinesChecker
from games.replacements_lines import (
    canonical_replacements_checker_line,
    parse_replacements_lines_text,
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
    def test_answer_accept_parallel_to_answers(self):
        left = '_X_ _Y_'
        ans = '_A|a_ _B_'
        p = parse_replacements_lines_text(left, ans)
        self.assertEqual(p['answers'], [['A', 'B']])
        self.assertEqual(p['answer_accept'], [[['A', 'a'], ['B']]])


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
