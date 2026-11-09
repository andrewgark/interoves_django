"""Spelling primitives shared by every part of the matcher."""

from __future__ import annotations

import unicodedata

# Size of the per-surface caches (readings, analyses). An entry costs
# ~0.6 KB; a daily article has ~6k distinct surfaces, so this keeps several
# articles and their guesses warm at ~20 MB per web process.
WORD_CACHE_SIZE = 32_768


def fold(text: str) -> str:
    """NFC, strip combining marks, casefold, ё→е.

    Stress marks are removed. Callers that need stress must read the raw
    string before folding.
    """
    folded = unicodedata.normalize('NFC', text or '')
    folded = ''.join(ch for ch in folded if unicodedata.category(ch) != 'Mn')
    return folded.casefold().replace('ё', 'е').strip()


def script_of(text: str) -> str:
    """Coarse Unicode script of the letters. Mixed tokens stay ``Zyyy``."""
    folded = fold(text)
    letters = [ch for ch in folded if ch.isalpha()]
    if not letters:
        return 'Zyyy'
    kinds = set()
    for ch in letters:
        if 'а' <= ch <= 'я' or ch == 'ё':
            kinds.add('Cyrl')
        elif 'a' <= ch <= 'z':
            kinds.add('Latn')
        elif 'Ͱ' <= ch <= 'Ͽ' or 'ἀ' <= ch <= '῿':
            kinds.add('Grek')
        else:
            kinds.add('Zyyy')
    if len(kinds) == 1:
        return next(iter(kinds))
    return 'Zyyy'
