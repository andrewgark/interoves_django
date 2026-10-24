"""Алфавитка: one posted word, and applying a daily guess or hint.

The send_attempt view still saves the attempt. The daily view still decides
access, the day number, completion, and HTML.
"""

from __future__ import annotations

from dataclasses import dataclass

from games.alphabetty.play import apply_guess, apply_hint
from games.puzzles.base import Puzzle


@dataclass(frozen=True)
class AlphabettyMove:
    word: str


@dataclass(frozen=True)
class AlphabettyMoveRefusal:
    status: str


def parse_move(post):
    """Read an alphabetty POST. ``word`` wins over ``text`` when it is present."""
    word = (post.get('word') or post.get('text') or '').strip()
    if not word:
        return AlphabettyMoveRefusal('empty')
    return AlphabettyMove(word)


def posted_guess(body, post):
    """Daily guess word: JSON ``word``, otherwise the form field. No stripping."""
    body = body or {}
    post = post or {}
    return body.get('word') or post.get('word') or ''


def apply_posted_guess(body, post, **play_kwargs):
    return apply_guess(word=posted_guess(body, post), **play_kwargs)


def apply_posted_hint(**play_kwargs):
    return apply_hint(**play_kwargs)


def attempt_advanced(attempt, last_state):
    """An alphabetty answer advances the clock only when it is accepted."""
    del last_state
    return attempt.status == 'Ok'


class AlphabettyPuzzle(Puzzle):
    task_type = 'alphabetty'

    parse_move = staticmethod(parse_move)
    posted_guess = staticmethod(posted_guess)
    apply_posted_guess = staticmethod(apply_posted_guess)
    apply_posted_hint = staticmethod(apply_posted_hint)
    attempt_advanced = staticmethod(attempt_advanced)
