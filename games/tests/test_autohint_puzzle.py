from types import SimpleNamespace

from django.test import SimpleTestCase

from games.puzzles import get_puzzle
from games.puzzles.core.short.autohint import AutohintPuzzle


class AutohintRuleTests(SimpleTestCase):
    def test_pending_and_wrong_open_a_hint(self):
        self.assertTrue(AutohintPuzzle.opens_on_wrong(SimpleNamespace(status='Pending')))
        self.assertTrue(AutohintPuzzle.opens_on_wrong(SimpleNamespace(status='Wrong')))

    def test_accepted_answer_does_not_open_a_hint(self):
        self.assertFalse(AutohintPuzzle.opens_on_wrong(SimpleNamespace(status='Ok')))

    def test_manual_hint_message_stays_the_same(self):
        self.assertEqual(
            AutohintPuzzle.manual_hint_message(),
            'Hints in this task can only be taken by answer submit',
        )

    def test_rule_is_not_a_check_attempt_hook(self):
        self.assertIsNone(get_puzzle('autohint'))
