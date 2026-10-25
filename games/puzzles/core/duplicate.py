"""How a puzzle reports a repeated submission.

Three answers, and only these: not a duplicate, a duplicate that does not name
the previous attempt, and a duplicate that does.
"""

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
