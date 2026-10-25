"""A posted attempt that is either stored text or a status the view returns."""

from __future__ import annotations

from dataclasses import dataclass


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
