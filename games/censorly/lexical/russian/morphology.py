"""Russian inflection identities from pymorphy dictionary parses.

The first parse is not the lexeme. A dictionary hit is ``(paradigm id,
normal form)``. Several of those may be live for one surface. Proper-name
readings lose to a common-noun reading at a comparable score, which is
why ``улей`` and ``улья`` meet. Predicted parses are not identities.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from games.censorly.lexical.core import fold

_PROPER = frozenset({'Name', 'Surn', 'Patr', 'Geox', 'Orgn', 'Trad'})


@dataclass(frozen=True)
class DictionaryReading:
    lemma: str
    para_id: int
    pos: str
    aspect: str
    score: float
    proper: bool


def readings(surface: str) -> tuple[DictionaryReading, ...]:
    """Plausible dictionary readings, best score first."""
    folded = fold(surface)
    if not folded:
        return ()
    return _readings(folded)


def lexeme_ids(surface: str) -> frozenset[tuple[int, str]]:
    """Inflection identities. Empty when the token is not in the dictionary."""
    return frozenset((item.para_id, item.lemma) for item in readings(surface))


def cognate_lemmas(surface: str) -> frozenset[str]:
    """Lemmas allowed into the cognate graph.

    A proper name, or a common word tied with the same lemma as a name,
    does not expand. ``улей`` stays: its name reading is a different lemma.
    """
    found = readings(surface)
    if not found or any(item.proper for item in found):
        return frozenset()
    if not any(item.pos for item in found):
        return frozenset()
    if _proper_shares_lemma(fold(surface)):
        return frozenset()
    return frozenset(item.lemma for item in found)


def cognate_lemma(surface: str) -> str:
    """Lemma allowed to enter the cognate graph, or ``''``.

    Several live lemmas (``прибыли``) or a stress pair that pymorphy cannot
    split (``строить``) do not expand. Stress itself is handled by the
    resolver before this function sees the string.
    """
    found = readings(surface)
    lemmas = {item.lemma for item in found}
    if len(lemmas) != 1:
        return ''
    return next(iter(lemmas))


@lru_cache(maxsize=524288)
def pos_of(lemma: str) -> str:
    found = readings(lemma)
    if not found:
        return ''
    return found[0].pos


def aspect_of(lemma: str) -> str:
    found = readings(lemma)
    aspects = {item.aspect for item in found if item.aspect}
    if len(aspects) == 1:
        return next(iter(aspects))
    return ''


@lru_cache(maxsize=524288)
def _readings(folded: str) -> tuple[DictionaryReading, ...]:
    from games.matcher.norm_matcher import MORPH_ANALYZER

    parsed = []
    for parse in MORPH_ANALYZER.parse(folded):
        if not parse.methods_stack:
            continue
        analyzer = parse.methods_stack[0][0]
        if type(analyzer).__name__ != 'DictionaryAnalyzer':
            continue
        _word, para_id, _idx = parse.methods_stack[0][1:]
        grams = parse.tag.grammemes
        parsed.append(DictionaryReading(
            lemma=fold(parse.normal_form),
            para_id=int(para_id),
            pos=_pos(grams),
            aspect='impf' if 'impf' in grams else 'perf' if 'perf' in grams else '',
            score=float(parse.score),
            proper=bool(grams & _PROPER),
        ))
    if not parsed:
        return ()
    common = [item for item in parsed if not item.proper]
    names = [item for item in parsed if item.proper]
    if common and names:
        common_best = max(item.score for item in common)
        name_best = max(item.score for item in names)
        if common_best >= 0.2 and common_best >= name_best - 0.05:
            parsed = common
        # A clearly stronger name reading keeps the proper pool.
        elif name_best > common_best + 0.05:
            parsed = names
    elif common:
        parsed = common
    best = max(item.score for item in parsed)
    kept = [item for item in parsed if item.score >= best - 0.15 and item.score >= 0.05]
    # One paradigm slot (nomn and accs) is one reading.
    unique: dict[tuple[int, str], DictionaryReading] = {}
    for item in kept:
        key = (item.para_id, item.lemma)
        previous = unique.get(key)
        if previous is None or item.score > previous.score:
            unique[key] = item
    ordered = sorted(unique.values(), key=lambda item: -item.score)
    return tuple(ordered)


def _proper_shares_lemma(folded: str) -> bool:
    """True when a name and a common noun of the same lemma are both plausible."""
    from games.matcher.norm_matcher import MORPH_ANALYZER

    common: dict[str, float] = {}
    named: dict[str, float] = {}
    for parse in MORPH_ANALYZER.parse(folded):
        if not parse.methods_stack:
            continue
        if type(parse.methods_stack[0][0]).__name__ != 'DictionaryAnalyzer':
            continue
        grams = parse.tag.grammemes
        lemma = fold(parse.normal_form)
        score = float(parse.score)
        bucket = named if grams & _PROPER else common
        bucket[lemma] = max(score, bucket.get(lemma, 0.0))
    for lemma, score in named.items():
        other = common.get(lemma)
        if other is not None and score >= other - 0.15:
            return True
    return False


def _pos(grams: set[str]) -> str:
    if grams & {'VERB', 'INFN'}:
        return 'VERB'
    if 'NUMR' in grams or 'Anum' in grams:
        return 'NUMR' if 'NUMR' in grams else 'ANUM'
    if grams & {'ADJF', 'ADJS'}:
        return 'ADJF'
    if 'NOUN' in grams:
        return 'NOUN'
    if 'ADVB' in grams:
        return 'ADVB'
    if 'NPRO' in grams:
        return 'NPRO'
    return ''
