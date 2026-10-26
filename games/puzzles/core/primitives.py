"""Shared attempt semantics: duplicate verdicts and move/refusal types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DuplicateVerdict:
    blocked: bool
    attempt_id: int | None = None


def same_text_verdict(attempt_text, other_text, other_attempt):
    """Same posted text names the previous attempt. Different text does not."""
    if attempt_text != other_text:
        return DuplicateVerdict(False)
    return DuplicateVerdict(True, getattr(other_attempt, 'id', None))


@dataclass(frozen=True)
class TextMove:
    text: str


@dataclass(frozen=True)
class MoveRefusal:
    status: str


@dataclass(frozen=True)
class MoveResponse:
    """The JSON body a puzzle may extend after a move is checked."""

    result: dict
    attempt: object
    attempt_persisted: bool
    user: object = None
    anon_key: object = None
    word_index: int | None = None
    word: str | None = None
