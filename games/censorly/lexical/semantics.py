"""Gameplay lexical matching without contextual word-sense choice.

A guess has no sentence. Every dictionary reading of the surface may open
a target. The target surface is treated the same way: article context does
not delete a grammatically possible reading.

Opening is existential. One agreeing pair of readings is enough.
Root structures must be equal as multisets. Sharing one root of a compound
does not open the compound.
"""

from __future__ import annotations

import re
from functools import lru_cache

from games.censorly.lexical.core import fold
from games.censorly.lexical.dispatcher import RUSSIAN, backend_name
from games.censorly.lexical.proper_names import PAIR_RELATIONS
from games.censorly.lexical.rootbank import REVIEWED_DERIVED_ROOTS, structures_of

_SERVICE = frozenset({'PREP', 'CONJ', 'PRCL', 'INTJ'})
_CONTENT = frozenset({
    'NOUN', 'VERB', 'INFN', 'ADJF', 'ADJS', 'NUMR', 'NPRO', 'ADVB',
    'GRND', 'PRTF', 'PRTS', 'COMP', 'PRED',
})
# Hyphen and non-breaking hyphen join a compound. Dashes do not.
_HYPHEN_RE = re.compile(r'[-\u2010\u2011]')
_DASH = frozenset('\u2013\u2014\u2015')

# Suppletion and ordinals that pymorphy or the root tables do not already
# join. Forms inside one paradigm (я/меня, люди/человек, лет/год, лучше/хороший)
# are same-lexeme and are not listed here.
_GRAMMAR_GROUPS = (
    ('один', 'первый'),
    ('два', 'второй'),
    ('три', 'третий'),
    ('четыре', 'четвертый'),
    ('пять', 'пятый'),
    ('шесть', 'шестой'),
    ('семь', 'седьмой'),
    ('восемь', 'восьмой'),
    ('девять', 'девятый'),
    ('десять', 'десятый'),
    ('плохой', 'хуже', 'худший'),
    ('хороший', 'лучше', 'лучший'),
    ('большой', 'больше', 'больший', 'наибольший'),
)

# One-word abbreviations. Not translations and not multi-word names.
# Language tags in wiki prose are tokenized without the trailing period
# («фр.» → surface «фр»), so list the bare stub here.
_ALIAS_GROUPS = (
    ('евросоюз', 'ес'),
    ('миллилитр', 'мл'),
    ('ватт', 'вт'),
    ('киловатт', 'квт'),
    ('мегаватт', 'мвт'),
    ('герц', 'гц'),
    ('внешторгбанк', 'втб'),
    ('автомобиль', 'авто'),
    ('факсимиле', 'факс'),
    ('университет', 'универ'),
    ('фотография', 'фото'),
    ('килограмм', 'кг'),
    ('километр', 'км'),
    ('сантиметр', 'см'),
    ('миллиметр', 'мм'),
    ('миллион', 'млн'),
    ('миллиард', 'млрд'),
    ('тысяча', 'тыс'),
    # Language labels after foreign glosses: (англ. …), (фр. …), …
    ('английский', 'англ'),
    ('французский', 'фр', 'франц'),
    ('немецкий', 'нем'),
    ('итальянский', 'итал'),
    ('испанский', 'исп'),
    ('португальский', 'порт'),
    ('латинский', 'лат'),
    ('греческий', 'греч'),
    ('китайский', 'кит'),
    ('японский', 'яп'),
    ('корейский', 'кор'),
    ('арабский', 'араб'),
    ('турецкий', 'тур'),
    ('персидский', 'перс'),
    ('украинский', 'укр'),
    ('белорусский', 'бел'),
    ('чешский', 'чеш'),
    ('словацкий', 'словацк'),
    ('венгерский', 'венг'),
    ('румынский', 'рум'),
    ('болгарский', 'болг'),
    ('сербский', 'серб'),
    ('хорватский', 'хорв'),
    ('нидерландский', 'нидерл'),
    ('голландский', 'голл'),
    ('шведский', 'швед'),
    ('норвежский', 'норв'),
    ('датский', 'дат'),
    ('финский', 'фин'),
    ('иврит', 'ивр'),
    ('санскрит', 'санскр'),
)

# Arabic digits and their Roman spelling for the same value. Direct pairs
# only: 20 opens XX, not XIX, and not the word двадцать.
_ROMAN_DIGITS = (
    (90, 'XC'), (50, 'L'), (40, 'XL'),
    (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I'),
)


def _roman(value: int) -> str:
    parts = []
    left = value
    for amount, glyph in _ROMAN_DIGITS:
        while left >= amount:
            parts.append(glyph)
            left -= amount
    return ''.join(parts)


_NUMERAL_ALIAS_GROUPS = tuple(
    (str(value), _roman(value)) for value in range(1, 100)
)

# Reserved for a later transliteration audit. Empty on purpose.
TRANSLIT_GROUPS: tuple[tuple[str, ...], ...] = ()


def opens(guess: str, target: str, *, article_language: str = 'ru') -> bool:
    return bool(explain(guess, target, article_language=article_language))


def explain(guess: str, target: str, *, article_language: str = 'ru') -> str:
    """``exact``, ``lexeme``, ``grammar``, ``alias``, ``proper``, ``root`` or ``''``."""
    left = fold(guess)
    right = fold(target)
    if not left or not right:
        return ''
    if left == right:
        return 'exact'
    if initially_open(guess) or initially_open(target):
        return ''
    if _alias(left, right):
        return 'alias'
    if _proper_pair(left, right):
        return 'proper'
    if not _russian(guess, article_language) or not _russian(target, article_language):
        return ''
    guess_reads = readings_of(guess)
    target_reads = readings_of(target)
    guess_lexemes = {(item.para_id, item.lemma) for item in guess_reads}
    target_lexemes = {(item.para_id, item.lemma) for item in target_reads}
    if guess_lexemes & target_lexemes:
        return 'lexeme'
    if _roots(guess_reads, guess) & _roots(target_reads, target):
        return 'root'
    if _grammar(guess_reads, target_reads):
        return 'grammar'
    return ''


def initially_open(surface: str) -> bool:
    """Prepositions, conjunctions, particles, interjections.

    A content reading within 0.15 of the best service reading keeps the
    token closed. Short pronouns are not opened by this rule.
    """
    found = readings_of(surface)
    if not found:
        return False
    best = max(item.score for item in found)
    if not any(item.pos in _SERVICE and item.score >= best - 1e-9 for item in found):
        return False
    service_best = max(item.score for item in found if item.pos in _SERVICE)
    content_scores = [item.score for item in found if item.pos in _CONTENT]
    if not content_scores:
        return True
    # One rival reading, or several that together rival the service tag.
    if max(content_scores) >= service_best - 0.15:
        return False
    if sum(content_scores) >= service_best - 1e-9:
        return False
    return True


@lru_cache(maxsize=262144)
def readings_of(surface: str) -> tuple:
    folded = fold(surface)
    if not folded:
        return ()
    from games.matcher.norm_matcher import MORPH_ANALYZER

    parsed = []
    seen = set()
    for parse in MORPH_ANALYZER.parse(folded):
        if not parse.methods_stack:
            continue
        if type(parse.methods_stack[0][0]).__name__ != 'DictionaryAnalyzer':
            continue
        _word, para_id, _idx = parse.methods_stack[0][1:]
        grams = parse.tag.grammemes
        pos = _pos(grams)
        lemma = fold(parse.normal_form)
        key = (int(para_id), lemma, pos)
        if key in seen:
            continue
        seen.add(key)
        parsed.append(_Reading(lemma, int(para_id), pos, float(parse.score)))
    return tuple(parsed)


@lru_cache(maxsize=262144)
def guess_readings_of(surface: str) -> tuple:
    """Readings to use for a word typed as a guess.

    A guess has no sentence context.  When its spelling is the dictionary
    form of one reading, prefer that reading over readings for which the same
    spelling is only an inflected form.  If it is not a citation form at all
    (for example ``прибыли``), retain every live reading.

    This is deliberately applied only to the guess side.  Article tokens are
    analyzed with their sentence context elsewhere and must keep their own
    possible identities.
    """
    found = readings_of(surface)
    folded = fold(surface)
    if not folded:
        return found
    citation = tuple(item for item in found if item.lemma == folded)
    return citation or found


def _russian(surface: str, article_language: str) -> bool:
    if backend_name(surface, article_language=article_language) == RUSSIAN:
        return True
    # The dispatcher drops weak readings. Gameplay keeps every dictionary hit
    # that still has a part of speech, except non-Russian scripts.
    from games.censorly.lexical.core import script_of
    if script_of(surface) != 'Cyrl' or article_language not in ('', 'ru'):
        return False
    if any(item.pos for item in readings_of(surface)):
        return True
    # Hyphenated compounds may be absent from pymorphy and still have roots.
    return bool(structures_of(fold(surface)) or _reviewed_derived_lemma(surface))


def _roots(found, surface: str) -> set[tuple[str, ...]]:
    keys = set()
    keys.update(structures_of(fold(surface)))
    for item in found:
        keys.update(structures_of(item.lemma))
    reviewed_lemma = _reviewed_derived_lemma(surface)
    if reviewed_lemma:
        keys.update(structures_of(reviewed_lemma))
    hyphen = _hyphen_structure(surface)
    if hyphen:
        keys.update(hyphen)
    return keys


def _reviewed_derived_lemma(surface: str) -> str:
    """Allow only explicitly reviewed lemmas through predicted inflections."""
    if not surface:
        return ''
    from games.matcher.norm_matcher import MORPH_ANALYZER

    for parse in MORPH_ANALYZER.parse(fold(surface)):
        lemma = fold(parse.normal_form)
        if lemma in REVIEWED_DERIVED_ROOTS:
            return lemma
    return ''


def _hyphen_structure(surface: str) -> tuple[tuple[str, ...], ...]:
    raw = surface or ''
    if any(char in raw for char in _DASH):
        return ()
    if not _HYPHEN_RE.search(raw):
        return ()
    # A dictionary compound already contributed its full structure.
    if structures_of(fold(raw)):
        return ()
    parts = [part for part in _HYPHEN_RE.split(raw) if part]
    if len(parts) < 2:
        return ()
    combos = [()]
    for part in parts:
        options = readings_of(part)
        structures = []
        for item in options:
            structures.extend(structures_of(item.lemma))
        if not structures:
            return ()
        combos = [prev + structure for prev in combos for structure in structures]
        if len(combos) > 32:
            return ()
    return tuple(tuple(sorted(combo)) for combo in combos)


def _grammar(left, right) -> bool:
    left_lemmas = {item.lemma for item in left}
    right_lemmas = {item.lemma for item in right}
    for group in _GRAMMAR:
        if left_lemmas & group and right_lemmas & group:
            return True
    return False


def _alias(left: str, right: str) -> bool:
    for group in _ALIASES:
        if left in group and right in group and left != right:
            return True
    return False


_PROPER_PAIRS = frozenset(
    frozenset((fold(left), fold(right)))
    for left, right in PAIR_RELATIONS
)


@lru_cache(maxsize=262144)
def _proper_pair(left: str, right: str) -> bool:
    direct = frozenset((fold(left), fold(right)))
    if direct in _PROPER_PAIRS:
        return True
    left_lemmas = {item.lemma for item in readings_of(left)}
    right_lemmas = {item.lemma for item in readings_of(right)}
    return any(
        frozenset((left_lemma, right_lemma)) in _PROPER_PAIRS
        for left_lemma in left_lemmas
        for right_lemma in right_lemmas
    )


def _pos(grams: set[str]) -> str:
    for name in (
        'PREP', 'CONJ', 'PRCL', 'INTJ', 'NPRO', 'PRED', 'NUMR',
        'ADVB', 'COMP', 'GRND', 'PRTF', 'PRTS', 'INFN', 'VERB',
        'ADJF', 'ADJS', 'NOUN',
    ):
        if name in grams:
            return name
    return ''


class _Reading:
    __slots__ = ('lemma', 'para_id', 'pos', 'score')

    def __init__(self, lemma: str, para_id: int, pos: str, score: float):
        self.lemma = lemma
        self.para_id = para_id
        self.pos = pos
        self.score = score


def _groups(rows: tuple[tuple[str, ...], ...]) -> tuple[frozenset[str], ...]:
    return tuple(frozenset(fold(item) for item in row) for row in rows)


_GRAMMAR = _groups(_GRAMMAR_GROUPS)
_ALIASES = _groups(_ALIAS_GROUPS + _NUMERAL_ALIAS_GROUPS + TRANSLIT_GROUPS)
