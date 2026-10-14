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

# Article-context corrections for valid homographs that the parser otherwise
# treats as both an inflection of the common word and a rare citation form.
# The key is (surface, lemma selected by tokenize_text); values are the only
# readings retained for that occurrence. Guess-side readings stay context-free.
TARGET_LEMMA_READINGS: dict[tuple[str, str], frozenset[str]] = {
    # In this spelling, «лет» is overwhelmingly the genitive plural of «год».
    # Retain the rare noun «лёт» only where the article lemmatizer selects it.
    ('лет', 'год'): frozenset({'год'}),
    # In the article sense «стёкла»/«стекла» is the plural of glass. The
    # parser also proposes the valid homograph «стечь»; retain that only when
    # the article lemmatizer actually selects it.
    ('стекла', 'стекло'): frozenset({'стекло'}),
}


def is_rejected(surface: str, lemma: str, pos: str) -> bool:
    """Whether this exact dictionary reading is ruled out for a spelling."""
    return (fold(surface), fold(lemma), pos) in REJECTED_READINGS


def target_readings(surface: str, article_lemma: str, readings):
    """Apply an explicit article-context reading correction, if one exists."""
    allowed = TARGET_LEMMA_READINGS.get((fold(surface), fold(article_lemma)))
    if allowed is None:
        return tuple(readings), False
    filtered = tuple(item for item in readings if fold(item.lemma) in allowed)
    return filtered, bool(filtered)
