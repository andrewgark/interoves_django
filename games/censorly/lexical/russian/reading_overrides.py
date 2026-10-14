"""Reviewed corrections to implausible dictionary readings.

These are surface-form corrections, separate from root-family assignments:
the morphology dictionary can list a spelling under an unrelated lemma even
when that spelling is not a valid form in Russian.
"""

from __future__ import annotations

from games.censorly.lexical.core import fold

# (surface, lemma, part of speech). Keep the noun «ум» and discard pymorphy's
# impossible verb reading «ум» → «умереть» (the verb forms are «умру», «умрёт»,
# «умер», etc.).
REJECTED_READINGS: frozenset[tuple[str, str, str]] = frozenset({
    ('ум', 'умереть', 'VERB'),
})


def is_rejected(surface: str, lemma: str, pos: str) -> bool:
    """Whether this exact dictionary reading is ruled out for a spelling."""
    return (fold(surface), fold(lemma), pos) in REJECTED_READINGS
