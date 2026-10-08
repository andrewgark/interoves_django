"""One-pass article index for the gameplay resolver.

A guess is analyzed once. Each target token is analyzed once per article
and then reused. Comparison is the same existential check as ``explain``.
"""

from __future__ import annotations

from functools import lru_cache

from games.censorly.lexical.core import fold
from games.censorly.lexical.rootbank import families_of
from games.censorly.lexical.semantics import (
    _alias,
    _grammar,
    _roots,
    _russian,
    initially_open,
    readings_of,
)

_REJECT_CAP = 200


class _Pack:
    __slots__ = ('fold', 'service', 'russian', 'lexemes', 'roots', 'reads', 'families')

    def __init__(self, surface: str):
        self.fold = fold(surface)
        self.service = initially_open(surface)
        self.reads = readings_of(surface)
        self.russian = _russian(surface, 'ru')
        self.lexemes = {(item.para_id, item.lemma) for item in self.reads}
        self.roots = _roots(self.reads, surface)
        families = set(families_of(self.fold))
        for item in self.reads:
            families.update(families_of(item.lemma))
        self.families = families


def _relation(guess: _Pack, target: _Pack) -> str:
    if not guess.fold or not target.fold:
        return ''
    if guess.fold == target.fold:
        return 'exact'
    if guess.service or target.service:
        return ''
    if _alias(guess.fold, target.fold):
        return 'alias'
    if not guess.russian or not target.russian:
        return ''
    if guess.lexemes & target.lexemes:
        return 'lexeme'
    if guess.roots & target.roots:
        return 'root'
    if _grammar(guess.reads, target.reads):
        return 'grammar'
    return ''


def _opens(guess: _Pack, target: _Pack) -> bool:
    return bool(_relation(guess, target))


def _root_ids(structures) -> set[str]:
    return {part for item in structures for part in item}


def _reject(guess: _Pack, target: _Pack) -> str:
    if guess.service or target.service or not guess.russian or not target.russian:
        return ''
    if _root_ids(guess.roots) & _root_ids(target.roots):
        return 'root_overlap'
    if guess.families & target.families:
        return 'family_split'
    return ''


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


def decision(tokens, guess: str) -> dict:
    """Opened targets and near-miss candidates for one guess.

    A candidate shares a root id or a dictionary family, but not a whole
    root structure. The lists are what later review should look at.
    """
    rows = tuple(
        ((tok.get('surface') or ''), (tok.get('lemma') or ''))
        for tok in tokens
        if tok.get('kind') == 'content'
    )
    if not rows or not (guess or '').strip():
        return {'readings': [], 'opened': [], 'rejected': [], 'rejected_total': 0}
    guess_pack = _Pack(guess)
    opened: dict[tuple, dict] = {}
    rejected: dict[tuple, dict] = {}
    for (surface, lemma), pack in zip(rows, _index(rows)):
        stored = lemma or pack.fold
        reason = _relation(guess_pack, pack)
        bucket = opened
        if not reason:
            reason = _reject(guess_pack, pack)
            bucket = rejected
        if not reason:
            continue
        key = (pack.fold, stored, reason)
        slot = bucket.get(key)
        if slot is None:
            slot = {'surface': surface, 'lemma': stored, 'reason': reason, 'count': 0}
            bucket[key] = slot
        slot['count'] += 1
    rejected_rows = list(rejected.values())
    return {
        'readings': [
            {'lemma': item.lemma, 'pos': item.pos, 'score': round(item.score, 3)}
            for item in guess_pack.reads
        ],
        'opened': list(opened.values()),
        'rejected': rejected_rows[:_REJECT_CAP],
        'rejected_total': len(rejected_rows),
    }
