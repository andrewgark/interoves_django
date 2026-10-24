"""Алфавитка: one posted word. The HTTP view still saves the attempt."""

from __future__ import annotations

from dataclasses import dataclass

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


class AlphabettyPuzzle(Puzzle):
    task_type = 'alphabetty'

    parse_move = staticmethod(parse_move)
