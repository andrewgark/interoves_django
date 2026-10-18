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
from games.censorly.lexical.semantic_splits import SENSE_COMPATIBLE
from games.censorly.lexical.russian.reading_overrides import is_rejected

# Function words open before a player has guessed anything. Interjections
# (INTJ) are lexical content in an article, not structural service words:
# «Эврика», «ура» and «ой» must remain masked until guessed.
_SERVICE = frozenset({'PREP', 'CONJ', 'PRCL'})
# Dictionary homographs that should not be treated as initially open service words.
_NEVER_INITIAL_OPEN = frozenset({
    'ла', 'де', 'ди', 'аль',
    # Short homographs and discourse/lexical words that must not be shown
    # before a player guesses them, despite a service-word reading.
    'фон', 'ван', 'дер', 'значит', 'короче', 'максимум',
})
# These common pronoun surfaces are intentionally visible despite competing
# dictionary readings. This is an initial-visibility decision only; it does
# not authorize their lexical homonyms or derivatives.
_FORCE_INITIAL_OPEN_PRONOUNS = frozenset({'это', 'ничего', 'нем'})
_DIGIT_CARDINAL_LEMMAS = {
    '0': 'ноль', '1': 'один', '2': 'два', '3': 'три',
    '4': 'четыре', '5': 'пять', '6': 'шесть', '7': 'семь',
    '8': 'восемь', '9': 'девять', '10': 'десять',
    '11': 'одиннадцать', '12': 'двенадцать', '13': 'тринадцать',
    '14': 'четырнадцать', '15': 'пятнадцать', '16': 'шестнадцать',
    '17': 'семнадцать', '18': 'восемнадцать', '19': 'девятнадцать',
    '20': 'двадцать', '30': 'тридцать', '40': 'сорок',
    '50': 'пятьдесят', '60': 'шестьдесят', '70': 'семьдесят',
    '80': 'восемьдесят', '90': 'девяносто', '100': 'сто',
    '200': 'двести', '300': 'триста', '400': 'четыреста',
    '500': 'пятьсот', '600': 'шестьсот', '700': 'семьсот',
    '800': 'восемьсот', '900': 'девятьсот', '1000': 'тысяча',
}
_INITIAL_CARDINAL_LEMMAS = frozenset(
    _DIGIT_CARDINAL_LEMMAS[str(number)] for number in range(10)
)
_INITIAL_ORDINAL_LEMMAS = frozenset({
    'первый', 'второй', 'третий', 'четвертый', 'пятый',
    'шестой', 'седьмой', 'восьмой', 'девятый',
})
_INITIAL_NUMBER_LEMMAS = _INITIAL_CARDINAL_LEMMAS | _INITIAL_ORDINAL_LEMMAS
_INITIAL_DIGIT_SURFACES = frozenset(str(number) for number in range(10))
_PRONOUN_ADJECTIVE_LEMMAS = frozenset({
    'этот', 'тот', 'такой', 'таков', 'таковой', 'столько', 'сколько',
    'несколько', 'какой', 'какой-нибудь', 'какой-либо', 'который',
    'чей', 'чей-нибудь', 'чей-либо', 'каков', 'каковой', 'весь',
    'всякий', 'каждый', 'любой', 'иной',
    'другой', 'сам', 'самый', 'мой', 'твой', 'наш', 'ваш', 'свой',
    'никакой', 'ничей', 'некоторый', 'некий', 'один', 'оба',
})
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
    # Approved one-word abbreviations from the offline abbreviation audit.
    # Keep every pair in its own group: abbreviation aliases are direct and
    # must not create transitive edges between unrelated full forms.
    ('килогерц', 'кгц'),
    ('мегагерц', 'мгц'),
    ('гигагерц', 'ггц'),
    ('терагерц', 'тгц'),
    ('килопаскаль', 'кпа'),
    ('мегапаскаль', 'мпа'),
    ('килоджоуль', 'кдж'),
    ('мегаджоуль', 'мдж'),
    ('гигабеккерель', 'гбк'),
    ('килобеккерель', 'кбк'),
    ('килокалория', 'ккал'),
    ('килоньютон', 'кн'),
    ('меганьютон', 'мн'),
    ('кандела', 'кд'),
    ('люкс', 'лк'),
    ('люмен', 'лм'),
    ('атмосфера', 'атм'),
    ('паскаль', 'па'),
    ('миллиграмм', 'мг'),
    ('микрограмм', 'мкг'),
    ('микрометр', 'мкм'),
    ('нанометр', 'нм'),
    ('милливольт', 'мв'),
    ('киловольт', 'кв'),
    ('килобит', 'кбит'),
    ('мегабит', 'мбит'),
    ('гигабит', 'гбит'),
    ('терабит', 'тбит'),
    ('петабит', 'пбит'),
    ('эксабит', 'эбит'),
    ('килобайт', 'кбайт', 'кб'),
    ('мегабайт', 'мбайт', 'мб'),
    ('гигабайт', 'гбайт', 'гб'),
    ('терабайт', 'тбайт', 'тб'),
    ('петабайт', 'пбайт', 'пб'),
    ('эксабайт', 'эбайт', 'эб'),
    ('медицинский', 'мед'),
    ('преподаватель', 'препод'),
    ('администратор', 'админ'),
    ('заместитель', 'зам'),
    ('заведующий', 'зав'),
    ('информация', 'инфа'),
    ('программа', 'прога'),
    ('лаборатория', 'лаба'),
    ('килограмм', 'кг'),
    ('километр', 'км'),
    ('сантиметр', 'см'),
    ('миллиметр', 'мм'),
    ('миллион', 'млн'),
    ('миллиард', 'млрд'),
    ('тысяча', 'тыс'),
    # Language labels after foreign glosses: (англ. …), (фр. …), …
    ('английский', 'англ'),
    ('русский', 'рус'),
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

_LETTER_NAME_LEMMAS = {'е': 'е', 'ш': 'ш'}


def _letter_name_readings(surface: str, found: tuple) -> tuple:
    lemma = _LETTER_NAME_LEMMAS.get(fold(surface))
    if not lemma:
        return found
    return tuple(item for item in found if item.lemma == lemma)


# Reserved for a later transliteration audit. Empty on purpose.
TRANSLIT_GROUPS: tuple[tuple[str, ...], ...] = ()


def opens(guess: str, target: str, *, article_language: str = 'ru') -> bool:
    return bool(explain(guess, target, article_language=article_language))


def explain(guess: str, target: str, *, article_language: str = 'ru') -> str:
    """Return the winning relation layer for a pair of surfaces."""
    left = fold(guess)
    right = fold(target)
    if not left or not right:
        return ''
    if left == right:
        return 'exact'
    numeric_root_match = _numeric_root_match(guess, target)
    if _alias(left, right):
        return 'alias'
    if (left.isdigit() or right.isdigit()) and not numeric_root_match:
        return ''
    if _proper_pair(left, right):
        return 'proper'
    if not _russian(guess, article_language) or not _russian(target, article_language):
        return ''
    if numeric_root_match:
        return 'root'
    # Prefer the reading appropriate to the spelling on both sides. This
    # removes non-citation homographs such as «разрыв» (noun vs gerund of
    # «разрыть») without removing noun/noun alternatives such as «улья».
    guess_reads = guess_readings_of(guess)
    target_reads = preferred_readings_of(target)
    target_root_reads = guess_readings_of(target)
    # SAME_LEXEME may use a second genuine noun reading (for example
    # «улья»/«улей»), but root matching stays citation-preferred so noun
    # «пара» does not open «испарять» through the inflected noun «пар».
    guess_lexemes = {
        (item.para_id, item.lemma)
        for item in preferred_readings_of(guess)
    }
    target_lexemes = {(item.para_id, item.lemma) for item in target_reads}
    if (initially_open(guess) or initially_open(target)) and not numeric_root_match:
        # Initial numeric forms keep their existing cardinal/ordinal grammar
        # relations (except the deliberately split one/first case).
        numeric_grammar = (
            _grammar(guess_reads, target_reads)
            and any(
                item.lemma in (_INITIAL_CARDINAL_LEMMAS - {'один'})
                for item in (*guess_reads, *target_reads)
            )
        )
        pronoun_lexeme = (
            bool(guess_lexemes & target_lexemes)
            and any(
                item.pos == 'NPRO'
                or item.lemma in _PRONOUN_ADJECTIVE_LEMMAS
                for item in (*guess_reads, *target_reads)
            )
        )
        if not (numeric_grammar or pronoun_lexeme):
            return ''
    if guess_lexemes & target_lexemes:
        return 'lexeme'
    guess_roots = _roots(guess_reads, guess)
    target_roots = _roots(target_root_reads, target)
    if guess_roots & target_roots:
        return 'root'
    if _root_sense_compatible(guess_roots, target_roots):
        return 'root_sense_compatible'
    if _grammar(guess_reads, target_reads):
        return 'grammar'
    return ''


def _numeric_root_match(guess: str, target: str) -> bool:
    # The special path exists for Arabic digit spellings, which otherwise do
    # not participate in the normal morphology/service-word flow.  Do not
    # let the shared cardinal root broaden ordinary lexical families such as
    # «один»/«однажды» or replace ordinal grammar relations.
    if not (fold(guess).isdigit() or fold(target).isdigit()):
        return False
    digit = fold(guess) if fold(guess).isdigit() else fold(target)
    other = target if digit == fold(guess) else guess
    cardinal = _DIGIT_CARDINAL_LEMMAS.get(digit)
    if not cardinal or not any(
        item.lemma == cardinal for item in guess_readings_of(other)
    ):
        return False
    common = _roots(guess_readings_of(guess), guess) & _roots(
        guess_readings_of(target), target,
    )
    # Compare the complete structure, including compound numerals such as
    # ``200``/«двести» ({дв, сот}).
    return bool(common)


def initially_open(surface: str, *, include_pronouns: bool = True) -> bool:
    """Function words, unambiguous pronouns, and 0–9 numerals start open.

    Personal pronouns use the NPRO tag. Pronoun-adjectives use a reviewed set
    of lemmas because pymorphy tags them as ADJF/NUMR. A competing reading
    within 15% of the pronoun reading keeps the token closed.
    """
    if fold(surface) in _NEVER_INITIAL_OPEN:
        return False
    if fold(surface) in _FORCE_INITIAL_OPEN_PRONOUNS:
        return True
    if fold(surface) in _INITIAL_DIGIT_SURFACES:
        return True
    found = readings_of(surface)
    if not found:
        return False
    if any(item.lemma in _INITIAL_NUMBER_LEMMAS for item in found):
        return True
    best = max(item.score for item in found)
    pronoun_scores = [
        item.score for item in found
        if item.pos == 'NPRO'
        or item.lemma in _PRONOUN_ADJECTIVE_LEMMAS
    ]
    if include_pronouns and pronoun_scores and max(pronoun_scores) >= best - 1e-9:
        pronoun_best = max(pronoun_scores)
        other_scores = [
            item.score for item in found
            if item.pos != 'NPRO' and item.lemma not in _PRONOUN_ADJECTIVE_LEMMAS
        ]
        if not other_scores or max(other_scores) < pronoun_best * 0.85:
            return True
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
    parsed = [
        item for item in parsed
        if not is_rejected(folded, item.lemma, item.pos)
    ]
    return tuple(parsed)


@lru_cache(maxsize=262144)
def guess_readings_of(surface: str) -> tuple:
    """Readings to use for a word typed as a guess.

    A guess has no sentence context.  When its spelling is the dictionary
    form of one reading, prefer that reading over readings for which the same
    spelling is only an inflected form.  If it is not a citation form at all
    (for example ``прибыли``), retain every live reading.

    The same helper is used for article-independent target analysis, so the
    two directions do not disagree merely because one word was typed first.
    """
    found = _letter_name_readings(surface, readings_of(surface))
    folded = fold(surface)
    if not folded:
        return found
    citation = tuple(item for item in found if item.lemma == folded)
    return citation or found


@lru_cache(maxsize=262144)
def preferred_readings_of(surface: str) -> tuple:
    """Readings suitable for an isolated gameplay token.

    If a spelling is itself a noun citation form, prefer that noun over a
    valid homographic participle, gerund, or short-adjective inflection. Keep
    other noun readings: they are genuine alternatives, e.g. ``улья`` as a
    rare noun and as the genitive of ``улей``.
    """
    found = _letter_name_readings(surface, readings_of(surface))
    folded = fold(surface)
    if not folded:
        return found
    citation = tuple(item for item in found if item.lemma == folded)
    if not citation:
        return found
    if any(item.pos == 'NOUN' for item in citation):
        return tuple(
            item for item in found
            if item.lemma == folded or item.pos == 'NOUN'
        )
    # A non-noun citation form may have a valid verb-like homograph: gerund
    # adverbs such as «стоя»/«стоять» are intentionally existentially open.
    # Only a competing noun citation is suppressed here (the «дорогой» side
    # of «дорога»); demonstrably bad morphology belongs in REJECTED_READINGS.
    if any(item.pos == 'NOUN' for item in found if item.lemma != folded):
        return citation
    return found


def _russian(surface: str, article_language: str) -> bool:
    if backend_name(surface, article_language=article_language) == RUSSIAN:
        return True
    # Reviewed Arabic numeral forms are lexical members of the corresponding
    # Russian numeral roots.  They are not Cyrillic, so the script gate below
    # would otherwise prevent root matching before structures are compared.
    if (surface or '').strip().isdigit() and structures_of(fold(surface)):
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


def _roots(
    found, surface: str, *, include_surface: bool = True,
) -> set[tuple[str, ...]]:
    keys = set()
    if include_surface:
        keys.update(structures_of(fold(surface)))
    for item in found:
        keys.update(structures_of(item.lemma))
    reviewed_lemma = _reviewed_derived_lemma(surface) if include_surface else ''
    if reviewed_lemma:
        keys.update(structures_of(reviewed_lemma))
    hyphen = _hyphen_structure(surface)
    if hyphen:
        keys.update(hyphen)
    return keys


def _root_sense_compatible(guess_structures, target_structures) -> bool:
    """Allow only explicitly reviewed sense compatibility within one root."""
    for guess_structure in guess_structures:
        guess_root = {
            part for part in guess_structure if not part.startswith('sense:')
        }
        guess_sense = {
            part for part in guess_structure if part.startswith('sense:')
        }
        if not guess_root or not guess_sense:
            continue
        for target_structure in target_structures:
            target_root = {
                part for part in target_structure if not part.startswith('sense:')
            }
            target_sense = {
                part for part in target_structure if part.startswith('sense:')
            }
            if (
                guess_root == target_root
                and frozenset(guess_sense | target_sense) in SENSE_COMPATIBLE
            ):
                return True
    return False


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


def _proper_components() -> dict[str, str]:
    """Map each explicitly related spelling to its connected component."""
    neighbors: dict[str, set[str]] = {}
    for pair in _PROPER_PAIRS:
        left, right = tuple(pair)
        neighbors.setdefault(left, set()).add(right)
        neighbors.setdefault(right, set()).add(left)

    component_by_word: dict[str, str] = {}
    for word in neighbors:
        if word in component_by_word:
            continue
        pending = [word]
        component = set()
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(neighbors[current] - component)
        component_id = min(component)
        component_by_word.update({member: component_id for member in component})
    return component_by_word


_PROPER_COMPONENT_BY_WORD = _proper_components()


@lru_cache(maxsize=262144)
def _proper_pair(left: str, right: str) -> bool:
    return _proper_pair_from_readings(
        left,
        right,
        readings_of(left),
        readings_of(right),
    )


def _proper_pair_from_readings(left, right, left_readings, right_readings) -> bool:
    """Check proper-name relation using the readings selected by the caller."""
    left_forms = {fold(left)} | {
        fold(item.lemma) for item in left_readings if item.lemma
    }
    right_forms = {fold(right)} | {
        fold(item.lemma) for item in right_readings if item.lemma
    }
    left_components = {
        _PROPER_COMPONENT_BY_WORD[form]
        for form in left_forms if form in _PROPER_COMPONENT_BY_WORD
    }
    return bool(left_components & {
        _PROPER_COMPONENT_BY_WORD[form]
        for form in right_forms if form in _PROPER_COMPONENT_BY_WORD
    })


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
