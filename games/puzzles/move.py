"""A posted attempt that is either stored text or a status the view returns."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextMove:
    text: str


@dataclass(frozen=True)
class MoveRefusal:
    status: str
