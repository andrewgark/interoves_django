"""pymorphy3 / OpenCorpora: the readings a spelling can have.

This is the only place in the matcher that calls the morphological
analyzer. It returns dictionary readings as they are; which of them the
game believes is decided by ``deciders.readings``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from games.puzzles.censorly.lexical.core import WORD_CACHE_SIZE, fold
from games.puzzles.censorly.lexical.sources.base import ExternalDictionary

_POS_ORDER = (
    'PREP', 'CONJ', 'PRCL', 'INTJ', 'NPRO', 'PRED', 'NUMR',
    'ADVB', 'COMP', 'GRND', 'PRTF', 'PRTS', 'INFN', 'VERB',
    'ADJF', 'ADJS', 'NOUN',
)
_PERSON_NAME = frozenset({'Name', 'Surn', 'Patr'})


@dataclass(frozen=True, slots=True)
class Reading:
    """One dictionary parse: lemma, paradigm, coarse part of speech, score.

    ``person_name`` marks a given name, surname or patronymic reading
    (OpenCorpora Name/Surn/Patr).
    """

    lemma: str
    para_id: int
    pos: str
    score: float
    person_name: bool = False


class Morphology(ExternalDictionary):
    name = 'pymorphy'

    def _load(self):
        from games.puzzles.core.norm_matcher import MORPH_ANALYZER

        return MORPH_ANALYZER

    def readings(self, surface: str) -> tuple[Reading, ...]:
        """Dictionary readings in analyzer order, one per (paradigm, lemma, POS).

        Predicted (out-of-dictionary) parses are not readings.
        """
        return _readings(fold(surface))

    def all_lemmas(self, surface: str) -> tuple[str, ...]:
        """Lemmas of every parse, predicted ones included, in analyzer order."""
        return _all_lemmas(fold(surface)) if surface else ()


@lru_cache(maxsize=WORD_CACHE_SIZE)
def _readings(folded: str) -> tuple[Reading, ...]:
    if not folded:
        return ()
    parsed = []
    seen = set()
    for parse in MORPHOLOGY.data.parse(folded):
        if not parse.methods_stack:
            continue
        if type(parse.methods_stack[0][0]).__name__ != 'DictionaryAnalyzer':
            continue
        _word, para_id, _idx = parse.methods_stack[0][1:]
        grams = parse.tag.grammemes
        pos = pos_of(grams)
        lemma = fold(parse.normal_form)
        key = (int(para_id), lemma, pos)
        if key in seen:
            continue
        seen.add(key)
        parsed.append(Reading(
            lemma, int(para_id), pos, float(parse.score),
            person_name=bool(grams & _PERSON_NAME),
        ))
    return tuple(parsed)


@lru_cache(maxsize=WORD_CACHE_SIZE)
def _all_lemmas(folded: str) -> tuple[str, ...]:
    return tuple(fold(parse.normal_form) for parse in MORPHOLOGY.data.parse(folded))


def pos_of(grams) -> str:
    for name in _POS_ORDER:
        if name in grams:
            return name
    return ''


MORPHOLOGY = Morphology()
