"""Language-neutral lexical nodes.

A node is an identity a backend can name. It is not a Russian lemma.
``language`` may be empty when the token's language is unknown.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

EXACT_MATCH = 'exact'
INFLECTION = 'inflection'
DERIVATION_GRAPH = 'derivation'


@dataclass(frozen=True)
class LexicalNode:
    """One addressable lexical item, or a generic normalized token."""

    backend: str
    key: str
    language: str = ''
    script: str = ''
    normalized: str = ''


def fold(text: str) -> str:
    """NFC, strip combining marks, casefold, ё→е.

    Stress marks are removed. Callers that need stress must read the raw
    string before folding.
    """
    folded = unicodedata.normalize('NFC', text or '')
    folded = ''.join(ch for ch in folded if unicodedata.category(ch) != 'Mn')
    return folded.casefold().replace('ё', 'е').strip()


def stress_signature(text: str) -> str:
    """Positions of combining marks. Empty when the token is unstressed."""
    folded = unicodedata.normalize('NFC', text or '')
    marks = []
    base = 0
    for char in folded:
        if unicodedata.category(char) == 'Mn':
            marks.append(str(base - 1))
        else:
            base += 1
    return ','.join(marks)


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
        elif '\u0370' <= ch <= '\u03ff' or '\u1f00' <= ch <= '\u1fff':
            kinds.add('Grek')
        else:
            kinds.add('Zyyy')
    if len(kinds) == 1:
        return next(iter(kinds))
    return 'Zyyy'
