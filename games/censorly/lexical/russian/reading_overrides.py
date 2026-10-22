"""Reviewed corrections to implausible dictionary readings.

These are surface-form corrections, separate from root-family assignments:
the morphology dictionary can list a spelling under an unrelated lemma even
when that spelling is not a valid form in Russian.
"""

from __future__ import annotations

from games.censorly.lexical.core import fold

NEM_PRONOUN_CONTEXT = '__censorly_nem_pronoun__'
NEM_NONPRONOUN_CONTEXT = '__censorly_nem_nonpronoun__'
N_ERA_CONTEXT = '__censorly_n_era__'
PO_SOFTWARE_CONTEXT = '__censorly_po_software__'
NEM_GERMAN_CONTEXT = '__censorly_nem_german__'
YEAR_ABBR_CONTEXT = '__censorly_year_abbr__'
CITY_ABBR_CONTEXT = '__censorly_city_abbr__'
PAGE_ABBR_CONTEXT = '__censorly_page_abbr__'
STREET_ABBR_CONTEXT = '__censorly_street_abbr__'
# Homographic language labels before a foreign gloss: «(кит. 北京)»,
# «от лат. felis». Each label has its own identity, so revealing «кит.» does
# not also reveal «тур.»; outside that position the ordinary word stays.
LANGUAGE_LABEL_LEMMAS = {
    'кит': 'китайский',
    'тур': 'турецкий',
    'бел': 'белорусский',
    'лат': 'латинский',
    'порт': 'португальский',
    'дат': 'датский',
}
LANGUAGE_LABEL_CONTEXTS = {
    label: f'__censorly_{label}_language__' for label in LANGUAGE_LABEL_LEMMAS
}

# (surface, lemma, part of speech). Keep the noun «ум» and discard pymorphy's
# impossible verb reading «ум» → «умереть» (the verb forms are «умру», «умрёт»,
# «умер», etc.).
REJECTED_READINGS: frozenset[tuple[str, str, str]] = frozenset({
    ('ум', 'умереть', 'VERB'),
    # «домой» is the adverb «домой», not a form of «домыть».  The latter
    # pymorphy reading lets washing forms leak the root «мы» into an article
    # token that should belong to the «дом» family.
    ('домой', 'домыть', 'VERB'),
    # In isolated gameplay «почти» and «зря» are the ordinary adverbs
    # «almost» and «in vain». Do not let rare verb/gerund analyses reconnect
    # them to the unrelated honor/seeing families.
    ('почти', 'почесть', 'VERB'),
    ('почти', 'почтить', 'VERB'),
    ('зря', 'зреть', 'GRND'),
    # In gameplay, «белых» is always the inflected adjective «белый».  The
    # fixed surname lemma «Белых» is not a useful lexical reading here and
    # otherwise suppresses the ordinary adjective citation reading.
    ('белых', 'белых', 'NOUN'),
    # A lone «е» in article text is a letter, an index or a Latin «E» from a
    # product name (Microsoft articles), never the archaic «е» = «есть». That
    # reading would let every form of «быть» open it.
    ('е', 'быть', 'VERB'),
})

# Article-context corrections for valid homographs that the parser otherwise
# treats as both an inflection of the common word and a rare citation form.
# The key is (surface, lemma selected by tokenize_text); values are the only
# readings retained for that occurrence. Guess-side readings stay context-free.
TARGET_LEMMA_READINGS: dict[tuple[str, str], frozenset[str]] = {
    # «статью» can also be the accusative of the rare noun «стать» (figure,
    # stature). When the article tokenizer selected «статья», keep only the
    # article reading so the become verb «стал» cannot open that target.
    ('статью', 'статья'): frozenset({'статья'}),
    ('стекла', 'стечь'): frozenset({'стечь'}),
    # «нем/нём» is a pronoun in a prepositional construction («о нём»,
    # «при нём»). The tokenizer assigns this identity per occurrence; a
    # standalone «нем.» is classified separately as the German abbreviation.
    ('нем', NEM_PRONOUN_CONTEXT): frozenset({'он', 'оно'}),
    ('нем', NEM_NONPRONOUN_CONTEXT): frozenset({'нем', 'немой'}),
    # In this spelling, «лет» is overwhelmingly the genitive plural of «год».
    # Retain the rare noun «лёт» only where the article lemmatizer selects it.
    ('лет', 'год'): frozenset({'год'}),
    # In the article sense «стёкла»/«стекла» is the plural of glass. The
    # parser also proposes the valid homograph «стечь»; retain that only when
    # the article lemmatizer actually selects it.
    ('стекла', 'стекло'): frozenset({'стекло'}),
    # The tokenizer resolves these article occurrences to their ordinary
    # lemmas. Pymorphy also proposes unrelated homographic readings
    # ("всей" → "всеять", "объём" → "объесть"), which must not leak
    # those roots into article matching.
    ('всей', 'весь'): frozenset({'весь'}),
    ('объем', 'объем'): frozenset({'объем'}),
    # In these article forms, the tokenizer selects the intended noun, while
    # the morphology analyzer also proposes a different root-family reading.
    ('западу', 'запад'): frozenset({'запад'}),
    ('правило', 'правило'): frozenset({'правило'}),
    ('моря', 'море'): frozenset({'море'}),
    # In article context «жене» is selected by the tokenizer as the dative
    # of «жена».  Pymorphy also exposes the dative of the name «Женя»;
    # retaining that alternative lets «Евгений» open the whole «жена» lemma.
    ('жене', 'жена'): frozenset({'жена'}),
    # Keep the noun occurrence «дорога» from inheriting the short-adjective
    # reading «дорога»/«дорогой» when article tokenization selected the noun.
    ('дорога', 'дорога'): frozenset({'дорога'}),
    # In article context these forms are forms of «выйти». Pymorphy also
    # exposes the homographic «выслать», whose send-sense must not make a
    # «послание» match an unrelated article sentence.
    ('вышел', 'выйти'): frozenset({'выйти'}),
    ('вышла', 'выйти'): frozenset({'выйти'}),
    ('вышли', 'выйти'): frozenset({'выйти'}),
    # The article tokenizer can occasionally choose «наслать» for the
    # homographic surface «нашли»; when it selected the intended «найти»,
    # discard the unrelated send reading.
    ('нашли', 'найти'): frozenset({'найти'}),
    # In article context «им» is the personal pronoun «они», not the rare
    # instrumental form of the noun «имя».
    ('им', 'они'): frozenset({'они'}),
    # «Трои» in article text is the proper name Troy. Do not let pymorphy's
    # homographic verb reading «троить» expose the numeral family.
    ('трои', 'троить'): frozenset(),
    # In this article form the selected adjective is «вяжущий»; discard the
    # unrelated participial reading of «вязать», which would reconnect it to
    # «связать» through the binding sense.
    ('вяжущих', 'вяжущий'): frozenset({'вяжущий'}),
}

# Some fixed abbreviations have a context-specific expansion rather than a
# normal pymorphy reading. Tokenization records these identities per occurrence;
# the matcher uses these citation lemmas without treating them as global aliases.
TARGET_LEMMA_OVERRIDES: dict[tuple[str, str], tuple[str, ...]] = {
    # «н. э.» = «нашей эры»: «н.» carries the possessive adjective «наш».
    ('н', N_ERA_CONTEXT): ('наш',),
    # These token identities encode parser decisions. The software acronym
    # intentionally has no lexical expansion: only the exact spelling opens it.
    ('по', PO_SOFTWARE_CONTEXT): (PO_SOFTWARE_CONTEXT,),
    ('нем', NEM_GERMAN_CONTEXT): ('немец',),
    ('нем', NEM_PRONOUN_CONTEXT): ('он', 'оно'),
    ('г', YEAR_ABBR_CONTEXT): ('год',),
    ('г', CITY_ABBR_CONTEXT): ('город',),
    ('стр', PAGE_ABBR_CONTEXT): ('страница',),
    ('ул', STREET_ABBR_CONTEXT): ('улица',),
    **{
        (label, LANGUAGE_LABEL_CONTEXTS[label]): (lemma,)
        for label, lemma in LANGUAGE_LABEL_LEMMAS.items()
    },
}

# Proper-name root assignment is family-level rather than a separate rule per
# case form.  Pymorphy maps several capitalized Troy forms to «троить»; the
# spelling/capitalization identifies the proper-name family in article text
# and for a capitalized guess.  Lowercase «трои» remains available to the
# ordinary numeral/verb morphology.
TROY_PROPER_ROOT_LEMMA = 'троя'
TROY_PROPER_FORMS = frozenset({'троя', 'трои', 'трою', 'троей', 'троею'})


def is_rejected(surface: str, lemma: str, pos: str) -> bool:
    """Whether this exact dictionary reading is ruled out for a spelling."""
    return (fold(surface), fold(lemma), pos) in REJECTED_READINGS


def target_readings(surface: str, article_lemma: str, readings):
    """Apply an explicit article-context reading correction, if one exists."""
    allowed = TARGET_LEMMA_READINGS.get((fold(surface), fold(article_lemma)))
    if allowed is None:
        return tuple(readings), False
    filtered = tuple(item for item in readings if fold(item.lemma) in allowed)
    # An explicit empty set deliberately suppresses all readings; it must not
    # fall back to the surface spelling's root structure.
    return filtered, True


def target_lemma_override(surface: str, article_lemma: str) -> tuple[str, ...]:
    """Return synthetic citation lemmas for a narrowly detected context."""
    return TARGET_LEMMA_OVERRIDES.get((fold(surface), article_lemma), ())


def guess_lemma_override(surface: str) -> tuple[str, ...]:
    """Return narrow citation lemmas for context-free guess analysis."""
    if surface and surface[0].isupper() and fold(surface) in TROY_PROPER_FORMS:
        return (TROY_PROPER_ROOT_LEMMA,)
    return ()


def proper_name_root_lemma(surface: str) -> str:
    """Return the reviewed proper-name root for a capitalized Troy form."""
    if surface and surface[0].isupper() and fold(surface) in TROY_PROPER_FORMS:
        return TROY_PROPER_ROOT_LEMMA
    return ''
