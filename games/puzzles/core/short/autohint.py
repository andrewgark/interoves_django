"""Автоподсказка: a wrong answer opens the next hint. The view still creates it."""

from __future__ import annotations

from games.puzzles.core.base import Puzzle


def opens_on_wrong(attempt):
    """True when a stored answer should reveal the next hint."""
    return attempt.status in ('Pending', 'Wrong')


def manual_hint_message():
    """The refusal shown when a player asks for the hint directly."""
    return 'Hints in this task can only be taken by answer submit'


class AutohintPuzzle(Puzzle):
    task_type = 'autohint'

    opens_on_wrong = staticmethod(opens_on_wrong)
    manual_hint_message = staticmethod(manual_hint_message)
