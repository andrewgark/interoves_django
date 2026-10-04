"""Rule-based Russian inflectional endings for Цензурки.

pymorphy3 supplies the part of speech and grammemes. The ending itself is
the longest case/person suffix allowed for that reading, not the paradigm
suffix and not the leftover after the lemma.

Policies that more than one school tradition could draw differently:

- No sentence context is available. The first pymorphy3 parse wins.
- «ь» and «й» in the nominative (ночь, герой, музей) stay in the stem.
  The nominative ending there is zero.
- Past-tense «-л-» and superlative «-ейш-/-айш-» stay in the stem.
  Masculine past (бежал) has a zero ending; бежала is бежал+а.
- Infinitive -ть/-ти/-чь and imperative -й/-и/-ьте are not endings.
- A reflexive postfix is kept on the tail: забегалась → забегал+ась,
  находился → находил+ся, делается → дела+ется,
  развивающихся → развивающ+ихся. The postfix is not an ending by itself,
  but the ending is not word-final without it.
- Gerunds are indeclinable: empty tail, postfix included.
- Comparatives, adverbs, prepositions, conjunctions, particles,
  predicatives, cardinal numerals, abbreviations and indeclinables
  (Fixd/Abbr) have an empty tail.
- Personal pronouns (1/2/3 person) are suppletive and have an empty tail.
  Other pronouns use the adjective ending tables.
- Short participles use the short-adjective endings (написан+∅, написана+а).
- Surnames and nouns with an adjectival paradigm (Толстой, животное,
  насекомое, данные) use the adjective tables. The paradigm is recognized
  when another form ends in ого/его/ых/их/ыми/ими/ому/ему. слой, музей and
  Николай stay unsplit: their й/ой belongs to the stem.
- A match must leave at least two stem letters, so мой is мо+й rather than м+ой.
  The tail may be as long as that stem, or longer: нового → ого, кажется → ется.
"""

from __future__ import annotations

from games.matcher.norm_matcher import MORPH_ANALYZER

_MIN_STEM = 2

# (number, case) → overt endings, longest match wins.
_NOUN_ENDINGS: dict[tuple[str, str], tuple[str, ...]] = {
    ('sing', 'nomn'): ('а', 'я', 'о', 'е'),
    ('sing', 'gent'): ('ы', 'и', 'а', 'я', 'у', 'ю'),
    ('sing', 'gen2'): ('у', 'ю'),
    ('sing', 'datv'): ('у', 'ю', 'е', 'и'),
    ('sing', 'ablt'): ('ою', 'ею', 'ью', 'ой', 'ей', 'ом', 'ем'),
    ('sing', 'loct'): ('е', 'и', 'у', 'ю'),
    ('sing', 'loc2'): ('у', 'ю', 'е', 'и'),
    ('plur', 'nomn'): ('ы', 'и', 'а', 'я', 'е'),
    ('plur', 'gent'): ('ов', 'ев', 'ей'),
    ('plur', 'datv'): ('ам', 'ям'),
    ('plur', 'ablt'): ('ами', 'ями', 'ьми'),
    ('plur', 'loct'): ('ах', 'ях'),
}

# Feminine accusative (столицу, землю) and animate masculine (героя, папу).
_NOUN_FEMN_ACCS = ('у', 'ю')
_NOUN_MASC_ANIM_ACCS = ('у', 'ю', 'а', 'я')

_ADJ_ENDINGS: dict[tuple[str, str, str], tuple[str, ...]] = {
    # Full adjectives, participles, ordinals, determiner-like pronouns.
    ('sing', 'masc', 'nomn'): ('ый', 'ий', 'ой', 'й'),
    ('sing', 'masc', 'gent'): ('ого', 'его'),
    ('sing', 'masc', 'datv'): ('ому', 'ему'),
    ('sing', 'masc', 'ablt'): ('ым', 'им'),
    ('sing', 'masc', 'loct'): ('ом', 'ем'),
    ('sing', 'femn', 'nomn'): ('ая', 'яя', 'а', 'я'),
    ('sing', 'femn', 'gent'): ('ой', 'ей'),
    ('sing', 'femn', 'datv'): ('ой', 'ей'),
    ('sing', 'femn', 'accs'): ('ую', 'юю', 'у', 'ю'),
    ('sing', 'femn', 'ablt'): ('ою', 'ею', 'ой', 'ей'),
    ('sing', 'femn', 'loct'): ('ой', 'ей'),
    ('sing', 'neut', 'nomn'): ('ое', 'ее', 'о', 'е'),
    ('sing', 'neut', 'gent'): ('ого', 'его'),
    ('sing', 'neut', 'datv'): ('ому', 'ему'),
    ('sing', 'neut', 'ablt'): ('ым', 'им'),
    ('sing', 'neut', 'loct'): ('ом', 'ем'),
    ('plur', '', 'nomn'): ('ые', 'ие', 'ы', 'и'),
    ('plur', '', 'gent'): ('ых', 'их'),
    ('plur', '', 'datv'): ('ым', 'им'),
    ('plur', '', 'ablt'): ('ыми', 'ими'),
    ('plur', '', 'loct'): ('ых', 'их'),
}

_SHORT_FEMN = ('а', 'я')
_SHORT_NEUT = ('о', 'е')
_SHORT_PLUR = ('ы', 'и')

_VERB_PERSONAL: dict[tuple[str, str], tuple[str, ...]] = {
    ('sing', '1per'): ('ю', 'у', 'м'),
    ('sing', '2per'): ('ешь', 'ишь', 'шь'),
    ('sing', '3per'): ('ет', 'ит', 'ст'),
    ('plur', '1per'): ('ем', 'им'),
    ('plur', '2per'): ('ете', 'ите'),
    ('plur', '3per'): ('ут', 'ют', 'ат', 'ят'),
}

_REFLEXIVE_POS = frozenset({'VERB', 'INFN', 'PRTF', 'PRTS', 'ADJF'})
# Another form in the lexeme ends like this → the noun declines as an adjective.
_ADJECTIVAL_LEXEME_TAILS = ('ого', 'его', 'ому', 'ему', 'ыми', 'ими', 'ых', 'их')
_ADJECTIVAL_NF: dict[str, bool] = {}
# Tied readings pick the longer tail only when every reading is inflected.
# A tied particle/adverb/preposition keeps pymorphy's first parse (это, ночью).
_INFLECTED_POS = frozenset({
    'NOUN', 'ADJF', 'ADJS', 'PRTF', 'PRTS', 'VERB', 'INFN', 'NPRO',
})
_EMPTY_POS = frozenset({
    'ADVB', 'COMP', 'CONJ', 'GRND', 'INTJ', 'NUMR', 'PRED', 'PREP', 'PRCL',
    'UNKN', 'LATN', 'ROMN',
})


def _match(word: str, endings: tuple[str, ...]) -> str:
    """Longest listed ending that leaves at least two stem letters."""
    best = ''
    for ending in endings:
        if len(ending) <= len(best):
            continue
        if word.endswith(ending) and len(word) - len(ending) >= _MIN_STEM:
            best = ending
    return best


def _adjectival_lexeme(parse) -> bool:
    """True when this noun's paradigm uses adjective endings (Толстой, животное)."""
    nf = parse.normal_form or ''
    cached = _ADJECTIVAL_NF.get(nf)
    if cached is not None:
        return cached
    ok = False
    try:
        for form in parse.lexeme:
            other = form.word or ''
            if other != nf and other.endswith(_ADJECTIVAL_LEXEME_TAILS):
                ok = True
                break
    except Exception:
        ok = False
    _ADJECTIVAL_NF[nf] = ok
    return ok


def _noun_ending(word: str, tag, parse=None) -> str:
    number = tag.number or ''
    case = tag.case or ''
    if not number or not case:
        return ''
    if case == 'accs':
        if number == 'plur':
            case = 'gent' if tag.animacy == 'anim' else 'nomn'
            tail = _match(word, _NOUN_ENDINGS.get((number, case), ()))
        elif tag.gender == 'femn':
            tail = _match(word, _NOUN_FEMN_ACCS)
        elif tag.gender == 'masc' and tag.animacy == 'anim':
            tail = _match(word, _NOUN_MASC_ANIM_ACCS)
        else:
            case = 'nomn'
            tail = _match(word, _NOUN_ENDINGS.get((number, case), ()))
    else:
        tail = _match(word, _NOUN_ENDINGS.get((number, case), ()))
    # данные would otherwise take the one-letter noun tail «е» instead of «ые».
    # слой matches «ой» only by spelling; its paradigm is nominal, so it stays.
    if parse is None:
        return tail
    adj = _adj_ending(word, tag)
    if len(adj) > len(tail) and _adjectival_lexeme(parse):
        return adj
    return tail


def _adj_ending(word: str, tag) -> str:
    number = 'plur' if tag.number == 'plur' else 'sing'
    gender = '' if number == 'plur' else (tag.gender or 'masc')
    case = tag.case or ''
    if not case:
        return ''
    if case == 'accs' and gender != 'femn':
        if tag.animacy == 'anim':
            case = 'gent'
        else:
            case = 'nomn'
    if case == 'gen2':
        case = 'gent'
    elif case == 'loc2':
        case = 'loct'
    return _match(word, _ADJ_ENDINGS.get((number, gender, case), ()))


def _short_ending(word: str, tag) -> str:
    """Short adjectives and short participles. Masculine tail is zero."""
    if tag.number == 'plur':
        return _match(word, _SHORT_PLUR)
    if tag.gender == 'femn':
        return _match(word, _SHORT_FEMN)
    if tag.gender == 'neut':
        return _match(word, _SHORT_NEUT)
    return ''


def _verb_ending(word: str, tag) -> str:
    # Imperative -й/-и/-ьте and infinitive -ть/-ти/-чь stay in the stem.
    if 'impr' in tag or tag.POS == 'INFN':
        return ''
    if tag.tense == 'past':
        # -л- is a formative suffix. Masculine past ending is zero.
        if tag.number == 'plur':
            return _match(word, ('и',))
        if tag.gender == 'femn':
            return _match(word, ('а',))
        if tag.gender == 'neut':
            return _match(word, ('о',))
        return ''
    if tag.tense in {'pres', 'futr'} or tag.person:
        return _match(word, _VERB_PERSONAL.get((tag.number or '', tag.person or ''), ()))
    return ''


def _split_reflexive(word: str) -> tuple[str, str]:
    if len(word) > 3 and word.endswith('ся'):
        return word[:-2], 'ся'
    if len(word) > 3 and word.endswith('сь'):
        return word[:-2], 'сь'
    return word, ''


def _ending_on(word: str, tag, parse=None) -> str:
    pos = tag.POS or ''
    if pos in _EMPTY_POS or 'Fixd' in tag or 'Abbr' in tag:
        return ''
    if pos == 'NOUN':
        return _noun_ending(word, tag, parse)
    if pos in {'ADJF', 'PRTF'}:
        return _adj_ending(word, tag)
    if pos in {'ADJS', 'PRTS'}:
        return _short_ending(word, tag)
    if pos in {'VERB', 'INFN'}:
        return _verb_ending(word, tag)
    if pos == 'NPRO':
        # он/его/им are suppletive. этого/этом decline like adjectives.
        if tag.person in {'1per', '2per', '3per'}:
            return ''
        return _adj_ending(word, tag)
    return ''


def _split_with_tag(word: str, tag, parse=None) -> tuple[str, str]:
    if 'Fixd' in tag or 'Abbr' in tag:
        return word, ''
    pos = tag.POS or ''
    if pos == 'GRND' or pos in _EMPTY_POS:
        return word, ''
    base, postfix = word, ''
    if pos in _REFLEXIVE_POS:
        base, postfix = _split_reflexive(word)
        # «весь» is an adjectival pronoun, not a reflexive form ending in
        # -сь. Verbs and participles do use the soft reflexive postfix.
        if pos == 'ADJF' and postfix == 'сь':
            base, postfix = word, ''
    tail = _ending_on(base, tag, parse) + postfix
    if not tail or len(word) - len(tail) < _MIN_STEM:
        return word, ''
    stem = word[:-len(tail)]
    if stem + tail != word:
        return word, ''
    return stem, tail


def grammatical_split(word: str) -> tuple[str, str]:
    """Return (stem, inflectional ending [+ reflexive postfix]).

    ``word`` must already be normalized (lowercase, ё→е). The stem is the
    word minus that tail. A zero ending is ``(word, '')``. This does not
    apply the censorly hint-length filter.

    Only the highest-scoring pymorphy reading is used. If several inflected
    readings share that score, the longer tail wins (мышью → ью, not ю).
    A tied indeclinable reading (particle, adverb, preposition) keeps the
    first parse, so «это» and «ночью» stay unsplit.
    """
    if not word:
        return '', ''
    try:
        parses = MORPH_ANALYZER.parse(word)
    except Exception:
        return word, ''
    if not parses:
        return word, ''
    best = parses[0].score
    top = [p for p in parses if abs(p.score - best) < 1e-9]
    if len(top) > 1 and all((p.tag.POS or '') in _INFLECTED_POS for p in top):
        chosen = max(top, key=lambda p: len(_split_with_tag(word, p.tag, p)[1]))
    else:
        chosen = top[0]
    return _split_with_tag(word, chosen.tag, chosen)
