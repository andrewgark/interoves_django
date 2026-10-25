"""Цензурка: applying one posted guess or hint.

Access, the day number, and HTML stay in the view. This class only turns the
posted move into the play result.
"""

from __future__ import annotations

from dataclasses import dataclass

from games.censorly.play import apply_guess, apply_hint
from games.puzzles.core.base import Puzzle


@dataclass(frozen=True)
class CensorlyMoveRefusal:
    error: str


def posted_guess(body, post):
    body = body or {}
    post = post or {}
    return body.get('word') or body.get('guess') or post.get('word') or ''


def posted_hint_token(body):
    """The hint token, or a refusal when the post does not name one."""
    try:
        return int((body or {}).get('token_id'))
    except (TypeError, ValueError):
        return CensorlyMoveRefusal('Укажите token_id')


def apply_posted_guess(body, post, **play_kwargs):
    return apply_guess(word=posted_guess(body, post), **play_kwargs)


def apply_posted_hint(body, **play_kwargs):
    token_id = posted_hint_token(body)
    if isinstance(token_id, CensorlyMoveRefusal):
        return token_id
    return apply_hint(token_id=token_id, **play_kwargs)


class CensorlyPuzzle(Puzzle):
    task_type = 'censorly'

    posted_guess = staticmethod(posted_guess)
    posted_hint_token = staticmethod(posted_hint_token)
    apply_posted_guess = staticmethod(apply_posted_guess)
    apply_posted_hint = staticmethod(apply_posted_hint)
