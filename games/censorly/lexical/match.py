"""One-pass article index for the gameplay resolver.

A guess is analyzed once. Each target token is analyzed once per article
and then reused. Comparison is the same existential check as ``explain``.
"""

from __future__ import annotations

from functools import lru_cache

from games.censorly.lexical.core import fold
from games.censorly.lexical.semantics import (
    _alias,
    _grammar,
    _roots,
    _russian,
    initially_open,
    readings_of,
)


class _Pack:
    __slots__ = ('fold', 'service', 'russian', 'lexemes', 'roots', 'reads')

    def __init__(self, surface: str):
        self.fold = fold(surface)
        self.service = initially_open(surface)
        self.reads = readings_of(surface)
        self.russian = _russian(surface, 'ru')
        self.lexemes = {(item.para_id, item.lemma) for item in self.reads}
        self.roots = _roots(self.reads, surface)


def _opens(guess: _Pack, target: _Pack) -> bool:
    if not guess.fold or not target.fold:
        return False
    if guess.fold == target.fold:
        return True
    if guess.service or target.service:
        return False
    if _alias(guess.fold, target.fold):
        return True
    if not guess.russian or not target.russian:
        return False
    if guess.lexemes & target.lexemes:
        return True
    if guess.roots & target.roots:
        return True
    if _grammar(guess.reads, target.reads):
        return True
    return False


@lru_cache(maxsize=8)
def _index(rows: tuple[tuple[str, str], ...]) -> tuple[_Pack, ...]:
    return tuple(_Pack(surface) for surface, _lemma in rows)


def matching_lemmas(tokens, guess: str) -> set[str]:
    """Stored lemmas of content tokens the guess opens."""
    rows = tuple(
        ((tok.get('surface') or ''), (tok.get('lemma') or ''))
        for tok in tokens
        if tok.get('kind') == 'content'
    )
    if not rows or not (guess or '').strip():
        return set()
    guess_pack = _Pack(guess)
    hits: set[str] = set()
    for (surface, lemma), pack in zip(rows, _index(rows)):
        if _opens(guess_pack, pack):
            hits.add(lemma or fold(surface) or guess_pack.fold)
    return hits
