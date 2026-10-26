"""Tokenize Wikipedia plaintext into censorly tokens."""

from __future__ import annotations

import re
import unicodedata
from itertools import groupby
from typing import Any

from games.puzzles.censorly.ids import CENSORLY_SPLITTER_VERSION
from games.puzzles.censorly.text.normalize import (
    WORD_CHARS,
    lemma_of,
    normalize_surface,
    split_stem_ending,
    strip_combining_marks,
)
from games.puzzles.censorly.lexical.russian.reading_overrides import (
    CITY_ABBR_CONTEXT,
    LANGUAGE_LABEL_CONTEXTS,
    NEM_GERMAN_CONTEXT,
    NEM_NONPRONOUN_CONTEXT,
    NEM_PRONOUN_CONTEXT,
    N_ERA_CONTEXT,
    PAGE_ABBR_CONTEXT,
    PO_SOFTWARE_CONTEXT,
    STREET_ABBR_CONTEXT,
    TELE_PREFIX_CONTEXT,
    YEAR_ABBR_CONTEXT,
)
from games.puzzles.censorly.text.stopwords import is_stop_word

# Private-use markers wrap wiki section titles after fetch (see wiki._headings_to_marked).
# Level is carried as "{level}\x1f{name}" so "==" / "===" stay distinct. No separator
# means an older marker: treat it as a section (level 2).
HEADING_START = '\ufdd0'
HEADING_END = '\ufdd1'
HEADING_LEVEL_SEP = '\x1f'
# A display formula is one visible punct token, not a row of letter masks.
FORMULA_START = '\ufdd2'
FORMULA_END = '\ufdd3'

_MATH_SERVICE_SYMBOLS = '°∫∬∭∮≤≥≠≈≡∈∉∑∏∂∇×·±∓→←↔⇌⟶⟵⟷⇒⇐⇔⟹⟸⟺↦⟨⟩₍₎∪∩∧∨√∞△⊥∥⊗∼⊆∣∠′˙¨¯ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ'
_MATH_SERVICE_SYMBOL_SET = frozenset(_MATH_SERVICE_SYMBOLS)
_MATH_SERVICE_CLASS = re.escape(_MATH_SERVICE_SYMBOLS)
# Mathematical and currency symbols are structural text, not hidden pictures.
# A few common marks live in Unicode's broad "Other Symbol" category alongside
# emoji and pictograms, so list only the marks that are safe to show.
_OPEN_SYMBOL_CATEGORIES = frozenset({'Sm', 'Sc'})
_OPEN_OTHER_SYMBOLS = frozenset({'^', '№', '©', '®', '℗', '™', '℠'})

# Greek, CJK, Cyrillic and Latin are words. Hyphen, ² and ₂ are not in the span.
_TOKEN_RE = re.compile(
    rf'[{WORD_CHARS}]+⃗|[{_MATH_SERVICE_CLASS}]|(?:(?![{_MATH_SERVICE_CLASS}])[{WORD_CHARS}])+|[^{WORD_CHARS}{_MATH_SERVICE_CLASS}\s]+|\s+',
)

_WORD_CORE_RE = re.compile(
    rf'^[{WORD_CHARS}]+⃗?$',
)

_HEADING_SPLIT_RE = re.compile(
    re.escape(HEADING_START) + r'(.*?)' + re.escape(HEADING_END),
    re.DOTALL,
)
_FORMULA_SPLIT_RE = re.compile(
    re.escape(FORMULA_START) + r'(.*?)' + re.escape(FORMULA_END),
    re.DOTALL,
)


def _strip_invisible(text: str) -> str:
    """Drop format characters that split a word (soft hyphen, ZWSP, BOM)."""
    return ''.join(
        ch for ch in (text or '')
        if unicodedata.category(ch) != 'Cf'
    )


def letter_length(surface: str) -> int:
    """Mask width: word-span characters, without combining marks."""
    n = 0
    for ch in unicodedata.normalize('NFC', surface or ''):
        if unicodedata.category(ch) == 'Mn':
            continue
        if _WORD_CORE_RE.fullmatch(ch):
            n += 1
    return n


def _kind_for_word(surface: str) -> str:
    plain = strip_combining_marks(surface)
    if is_stop_word(plain):
        return 'stop'
    return 'content'


def _split_heading_marker(raw: str) -> tuple[int, str]:
    """Return (level 2–6, title). Missing separator keeps the whole string at level 2."""
    text = strip_combining_marks((raw or '').strip())
    level = 2
    if HEADING_LEVEL_SEP in text:
        level_s, name = text.split(HEADING_LEVEL_SEP, 1)
        if level_s.isdigit():
            level = int(level_s)
        text = strip_combining_marks(name.strip())
    return min(max(level, 2), 6), text


def _heading_break(tid: int) -> dict[str, Any]:
    return {
        'id': tid,
        'surface': '',
        'kind': 'heading_break',
        'lemma': '',
        'length': 0,
        'in_title': False,
        'in_heading': False,
    }


def _formula_token(surface: str, tid: int, *, in_title: bool, in_heading: bool) -> dict[str, Any]:
    return {
        'id': tid,
        'surface': surface,
        'kind': 'punct',
        'lemma': '',
        'length': 0,
        'in_title': bool(in_title),
        'in_heading': bool(in_heading),
    }


def _tokenize_chunk(
    text: str,
    *,
    in_title: bool,
    start_id: int,
    in_heading: bool = False,
) -> list[dict[str, Any]]:
    """Words, spaces, punctuation, and whole formulas as one visible token."""
    tokens: list[dict[str, Any]] = []
    tid = start_id
    pos = 0
    for match in _FORMULA_SPLIT_RE.finditer(text or ''):
        if match.start() > pos:
            chunk = _tokenize_plain(
                text[pos:match.start()],
                in_title=in_title,
                start_id=tid,
                in_heading=in_heading,
            )
            tokens.extend(chunk)
            tid += len(chunk)
        surface = re.sub(r'\s+', ' ', match.group(1) or '').strip()
        if surface:
            tokens.append(_formula_token(
                surface, tid, in_title=in_title, in_heading=in_heading,
            ))
            tid += 1
        pos = match.end()
    if pos < len(text or ''):
        tokens.extend(_tokenize_plain(
            (text or '')[pos:],
            in_title=in_title,
            start_id=tid,
            in_heading=in_heading,
        ))
    elif pos == 0:
        tokens.extend(_tokenize_plain(
            text or '',
            in_title=in_title,
            start_id=tid,
            in_heading=in_heading,
        ))
    return tokens


def _plain_token_surfaces(text: str):
    """Keep punctuation outside masked symbol runs (e.g. «♕),»)."""
    for match in _TOKEN_RE.finditer(text):
        raw = match.group(0)
        if raw.isspace() or _WORD_CORE_RE.fullmatch(raw):
            yield raw
        else:
            for _is_symbol, chars in groupby(
                raw, key=lambda char: unicodedata.category(char).startswith('S'),
            ):
                yield ''.join(chars)


def _is_open_symbol_run(raw: str) -> bool:
    """True for structural symbols that should remain visible in the article."""
    return bool(raw) and all(
        char in _MATH_SERVICE_SYMBOL_SET
        or char in _OPEN_OTHER_SYMBOLS
        or unicodedata.category(char) in _OPEN_SYMBOL_CATEGORIES
        for char in raw
    )


def _tokenize_plain(
    text: str,
    *,
    in_title: bool,
    start_id: int,
    in_heading: bool = False,
) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    tid = start_id
    cleaned = unicodedata.normalize('NFC', _strip_invisible(text))
    for raw in _plain_token_surfaces(cleaned):
        if not raw:
            continue
        # Drop combining accents from play surfaces (guessing / display).
        surface = raw if raw.isspace() or raw.endswith('⃗') else strip_combining_marks(raw)
        if not surface and not raw.isspace():
            continue
        if surface.isspace():
            kind = 'space'
            lemma = ''
            length = 0
            ending = ''
            stem_length = 0
        elif _WORD_CORE_RE.fullmatch(raw) or _WORD_CORE_RE.fullmatch(surface):
            software_acronym = surface == 'ПО'
            kind = 'content' if software_acronym else _kind_for_word(surface)
            plain = strip_combining_marks(surface)
            lemma = (
                PO_SOFTWARE_CONTEXT if software_acronym else
                lemma_of(plain) if kind == 'content' else normalize_surface(plain)
            )
            length = letter_length(surface)
            ending = ''
            stem_length = length
            if kind == 'content' and not software_acronym:
                _stem, ending = split_stem_ending(surface)
                stem_length = letter_length(_stem) if ending else length
        elif (
            not _is_open_symbol_run(raw)
            and any(unicodedata.category(char).startswith('S') for char in raw)
        ):
            # Hide pictographic symbols (chess pieces, emoji, etc.), while
            # keeping the explicitly listed math service glyphs visible.
            kind = 'symbol'
            lemma = ''
            length = sum(
                1 for char in raw
                if unicodedata.category(char).startswith('S')
            )
            ending = ''
            stem_length = length
        else:
            kind = 'stop' if is_stop_word(surface) else 'punct'
            lemma = ''
            length = 0
            ending = ''
            stem_length = 0
        tok: dict[str, Any] = {
            'id': tid,
            'surface': surface,
            'kind': kind,
            'lemma': lemma,
            'length': length,
            'in_title': bool(in_title),
            'in_heading': bool(in_heading),
        }
        if kind == 'content' and ending:
            tok['ending'] = ending
            tok['stem_length'] = stem_length
        if kind == 'content' and surface == 'ПО':
            tok['context_reading'] = PO_SOFTWARE_CONTEXT
            tok['initially_open'] = False
        tokens.append(tok)
        tid += 1
    return tokens


def tokenize_text(text: str, *, in_title: bool = False, start_id: int = 0) -> list[dict[str, Any]]:
    """Split plaintext into tokens; wiki headings → word tokens with in_heading."""
    raw = unicodedata.normalize('NFC', text or '')
    tokens: list[dict[str, Any]] = []
    tid = start_id
    pos = 0
    for match in _HEADING_SPLIT_RE.finditer(raw):
        if match.start() > pos:
            chunk = _tokenize_chunk(raw[pos:match.start()], in_title=in_title, start_id=tid)
            tokens.extend(chunk)
            tid = tid + len(chunk)
        level, heading = _split_heading_marker(match.group(1) or '')
        if heading:
            tokens.append(_heading_break(tid))
            tid += 1
            chunk = _tokenize_chunk(
                heading, in_title=False, start_id=tid, in_heading=True,
            )
            for tok in chunk:
                tok['heading_level'] = level
            tokens.extend(chunk)
            tid = tid + len(chunk)
            tokens.append(_heading_break(tid))
            tid += 1
        pos = match.end()
    if pos < len(raw):
        chunk = _tokenize_chunk(raw[pos:], in_title=in_title, start_id=tid)
        tokens.extend(chunk)
    elif pos == 0 and not tokens:
        tokens = _tokenize_chunk(raw, in_title=in_title, start_id=start_id)
    return _annotate_contextual_readings(tokens)


_PREPOSITION_SURFACES = frozenset({
    'без', 'в', 'во', 'вместо', 'вне', 'для', 'до', 'за', 'из', 'изо',
    'из-за', 'из-под', 'к', 'ко', 'кроме', 'между', 'меж', 'на', 'над',
    'надо', 'несмотря', 'о', 'об', 'обо', 'от', 'ото', 'перед', 'передо',
    'по', 'под', 'подо', 'при', 'про', 'ради', 'с', 'со', 'сквозь', 'среди',
    'у',
})
_PRONOUN_MODIFIERS = frozenset({
    'сам', 'сама', 'само', 'сами', 'самого', 'самой', 'самому', 'самим',
    'самом', 'самих', 'самими', 'один', 'одна', 'одно', 'одни', 'одного',
    'одной', 'одном', 'одному', 'одним', 'одних', 'одними',
})


def _next_nonspace(tokens: list[dict[str, Any]], index: int) -> int:
    while index < len(tokens) and tokens[index].get('kind') == 'space':
        index += 1
    return index


def _previous_nonspace(tokens: list[dict[str, Any]], index: int) -> int:
    index -= 1
    while index >= 0 and tokens[index].get('kind') == 'space':
        index -= 1
    return index


def _is_preposition_governing_pronoun(tokens: list[dict[str, Any]], index: int) -> bool:
    """Recognize a preposition before «нем» with optional «сам»/«один» forms."""
    cursor = _previous_nonspace(tokens, index)
    while cursor >= 0:
        token = tokens[cursor]
        if token.get('kind') not in {'content', 'stop'}:
            return False
        surface = normalize_surface(token.get('surface') or '')
        if surface in _PREPOSITION_SURFACES:
            return True
        if surface not in _PRONOUN_MODIFIERS:
            return False
        cursor = _previous_nonspace(tokens, cursor)
    return False


def _is_language_label_position(
    tokens: list[dict[str, Any]], previous: int, next_word: int,
) -> bool:
    """Recognize «(кит. …», «; лат. …», «от лат. …» or a non-Cyrillic gloss."""
    if previous >= 0:
        token = tokens[previous]
        surface = token.get('surface') or ''
        if token.get('kind') == 'punct' and surface.endswith(('(', ',', ';')):
            return True
        if normalize_surface(surface) in {'от', 'из'}:
            return True
    if next_word < len(tokens):
        first = (tokens[next_word].get('surface') or '')[:1]
        return first.isalpha() and not ('а' <= first.lower() <= 'я' or first.lower() == 'ё')
    return False


def _is_existential_est(tokens: list[dict[str, Any]], index: int) -> bool:
    """Select «быть» for copular/existential «есть», not the verb «есть» food."""
    previous = _previous_nonspace(tokens, index)
    following = _next_nonspace(tokens, index + 1)
    if following >= len(tokens):
        return False
    right = tokens[following]

    from games.puzzles.core.norm_matcher import MORPH_ANALYZER

    def parses(token):
        surface = normalize_surface(token.get('surface') or '')
        return tuple(
            parse for parse in MORPH_ANALYZER.parse(surface)
            if parse.methods_stack
            and type(parse.methods_stack[0][0]).__name__ == 'DictionaryAnalyzer'
        )

    nominal_pos = {'NOUN', 'NPRO', 'ADJF', 'ADJS', 'PRTF', 'PRTS'}

    # A nominative predicate after «есть» is the clearest signal («есть
    # вратарь»). Ignore forms that can also be accusative unless the left side
    # supports a copula; «пора есть суп» has a predicate «пора» and an object.
    if right.get('kind') == 'content':
        right_parses = parses(right)
        right_nominative = any(
            parse.tag.POS in nominal_pos and 'nomn' in parse.tag.grammemes
            for parse in right_parses
        )
        right_accusative = any(
            parse.tag.POS in nominal_pos and 'accs' in parse.tag.grammemes
            for parse in right_parses
        )
        if right_nominative and not right_accusative:
            return True
        if right_nominative and previous >= 0 and tokens[previous].get('kind') == 'content':
            left_parses = parses(tokens[previous])
            if any(parse.tag.POS == 'PRED' for parse in left_parses):
                return False
            if any(
                parse.tag.POS in {'NOUN', 'NPRO'}
                and 'nomn' in parse.tag.grammemes
                for parse in left_parses
            ):
                return True

        # Let an unambiguous nominative head resolve a preceding adjective:
        # «есть хороший вратарь».
        if any(parse.tag.POS in {'ADJF', 'ADJS', 'PRTF', 'PRTS'} for parse in right_parses):
            cursor = following
            for _ in range(4):
                cursor = _next_nonspace(tokens, cursor + 1)
                if cursor >= len(tokens) or tokens[cursor].get('kind') != 'content':
                    break
                head_parses = parses(tokens[cursor])
                if any(
                    parse.tag.POS in {'NOUN', 'NPRO'}
                    and 'nomn' in parse.tag.grammemes
                    and 'accs' not in parse.tag.grammemes
                    for parse in head_parses
                ):
                    return True
                if not any(
                    parse.tag.POS in {'ADJF', 'ADJS', 'PRTF', 'PRTS'}
                    for parse in head_parses
                ):
                    break

    # An oblique phrase can follow existential «есть» («еда есть на столе»),
    # but only after a nominative subject and a locative preposition.
    if right.get('kind') != 'stop' or normalize_surface(
        right.get('surface') or ''
    ) not in {'в', 'во', 'на', 'над', 'под', 'за', 'перед', 'при', 'у', 'около'}:
        return False

    cursor = previous
    while cursor >= 0:
        token = tokens[cursor]
        if token.get('kind') == 'punct' and token.get('surface') == ',':
            cursor = _previous_nonspace(tokens, cursor)
            continue
        if token.get('kind') == 'stop' and normalize_surface(
            token.get('surface') or ''
        ) in {'и', 'а', 'но'}:
            cursor = _previous_nonspace(tokens, cursor)
            continue
        break
    if cursor < 0 or tokens[cursor].get('kind') != 'content':
        return False
    left_parses = parses(tokens[cursor])
    return any(
        parse.tag.POS in {'NOUN', 'NPRO'}
        and 'nomn' in parse.tag.grammemes
        and 'accs' not in parse.tag.grammemes
        for parse in left_parses
    )


def _is_coma_context(tokens: list[dict[str, Any]], index: int) -> bool:
    """Distinguish accusative «кому» from dative «кому» = «кто».

    «В кому», including agreeing modifiers («в глубокую кому»), selects the noun.
    A dative preposition («к кому», «по кому») does not resolve the homograph.
    """
    following = _next_nonspace(tokens, index + 1)
    if following < len(tokens) and normalize_surface(
        tokens[following].get('surface') or ''
    ) in {'-', '‐', '‑', 'угодно'}:
        # «В знакомую кому-то / кому угодно историю»: a dative complement.
        return False

    from games.puzzles.core.norm_matcher import MORPH_ANALYZER

    cursor = index
    while cursor > 0:
        previous = _previous_nonspace(tokens, cursor)
        if previous < 0 or tokens[previous].get('kind') not in {'content', 'stop'}:
            return False
        # Do not connect words from different paragraphs or list items.
        if any(
            any(char in token.get('surface', '') for char in '\n\r\u2028\u2029')
            for token in tokens[previous + 1:cursor]
        ):
            return False
        surface = normalize_surface(tokens[previous].get('surface') or '')
        if surface in {'в', 'во'}:
            return True
        if not any(
            parse.tag.POS in {'ADJF', 'PRTF'}
            and {'femn', 'sing', 'accs'} <= parse.tag.grammemes
            for parse in MORPH_ANALYZER.parse(surface)
        ):
            return False
        cursor = previous
    return False


def _annotate_contextual_readings(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach high-confidence, occurrence-specific readings before matching."""
    for index, tok in enumerate(tokens):
        if tok.get('kind') != 'content':
            continue
        surface = tok.get('surface') or ''
        word = normalize_surface(surface)
        previous = _previous_nonspace(tokens, index)
        after_word = _next_nonspace(tokens, index + 1)
        has_period = (
            after_word < len(tokens)
            and tokens[after_word].get('kind') == 'punct'
            and tokens[after_word].get('surface') == '.'
        )
        after_period = _next_nonspace(tokens, after_word + 1) if has_period else len(tokens)

        if word == 'есть' and _is_existential_est(tokens, index):
            tok['lemma'] = 'быть'
            tok['context_reading'] = 'verb:быть'
            tok['initially_open'] = False
            continue

        if word == 'кому' and _is_coma_context(tokens, index):
            tok['lemma'] = 'кома'
            tok['context_reading'] = 'noun:кома'
            tok['initially_open'] = False
            continue

        # In «вода стекла с крыши» the word is the past-tense verb «стечь»;
        # a noun phrase such as «осколки стекла с рисунком» must stay «стекло».
        # Keep this deliberately narrow until we have syntactic case analysis.
        if word == 'стекла' and previous >= 0:
            previous_token = tokens[previous]
            following = tokens[after_word] if after_word < len(tokens) else {}
            following_surface = normalize_surface(following.get('surface') or '')
            if (
                previous_token.get('lemma') == 'вода'
                and following_surface in {'с', 'со', 'из', 'изо', 'в', 'во', 'вниз'}
            ):
                tok['lemma'] = 'стечь'
                tok['context_reading'] = 'verb:стечь'
                tok['initially_open'] = False
                continue

        # «нем.» is the abbreviation for German only without ё and outside a
        # prepositional phrase. A preceding preposition wins over the period.
        if word == 'нем':
            if _is_preposition_governing_pronoun(tokens, index) or 'ё' in surface.lower():
                tok['lemma'] = NEM_PRONOUN_CONTEXT
                tok['context_reading'] = NEM_PRONOUN_CONTEXT
            elif has_period:
                tok['lemma'] = NEM_GERMAN_CONTEXT
                tok['context_reading'] = NEM_GERMAN_CONTEXT
                tok['initially_open'] = False
            else:
                tok['lemma'] = NEM_NONPRONOUN_CONTEXT
                tok['context_reading'] = NEM_NONPRONOUN_CONTEXT
                tok['initially_open'] = False
            continue

        # «кит.», «тур.», «лат.» are language labels only before a gloss;
        # «синий кит.» at the end of a sentence stays the animal.
        label_context = LANGUAGE_LABEL_CONTEXTS.get(word)
        if (
            label_context and has_period
            and _is_language_label_position(tokens, previous, after_period)
        ):
            tok['lemma'] = label_context
            tok['context_reading'] = label_context
            tok['initially_open'] = False
            continue

        # «теле-шоу» splits into «теле» and «шоу»; «в теле» stays «тело».
        if (
            word == 'теле'
            and index + 1 < len(tokens)
            and tokens[index + 1].get('kind') == 'punct'
            and tokens[index + 1].get('surface') == '-'
        ):
            tok['lemma'] = TELE_PREFIX_CONTEXT
            tok['context_reading'] = TELE_PREFIX_CONTEXT
            continue

        # The two-letter abbreviation is case-sensitive; sentence-initial
        # «По» remains the ordinary preposition.
        if surface == 'ПО':
            tok['lemma'] = PO_SOFTWARE_CONTEXT
            tok['context_reading'] = PO_SOFTWARE_CONTEXT
            tok['initially_open'] = False
            continue

        # «н. э.» has an established contextual reading (“нашей эры”).
        if word == 'н' and has_period and after_period < len(tokens):
            e_index = _next_nonspace(tokens, after_period)
            if (
                e_index < len(tokens)
                and tokens[e_index].get('kind') == 'content'
                and normalize_surface(tokens[e_index].get('surface') or '') == 'э'
            ):
                e_dot = _next_nonspace(tokens, e_index + 1)
                if (
                    e_dot < len(tokens)
                    and tokens[e_dot].get('kind') == 'punct'
                    and tokens[e_dot].get('surface') == '.'
                ):
                    tok['lemma'] = N_ERA_CONTEXT
                    tok['context_reading'] = N_ERA_CONTEXT
                    tok['initially_open'] = False
                    continue

        if not has_period:
            continue
        next_word_index = after_period
        next_surface = (
            tokens[next_word_index].get('surface') or ''
            if next_word_index < len(tokens) else ''
        )
        if word == 'г' and previous >= 0 and (tokens[previous].get('surface') or '').isdigit():
            tok['lemma'] = YEAR_ABBR_CONTEXT
            tok['context_reading'] = YEAR_ABBR_CONTEXT
            tok['initially_open'] = False
        elif word == 'г' and next_surface[:1].isupper():
            tok['lemma'] = CITY_ABBR_CONTEXT
            tok['context_reading'] = CITY_ABBR_CONTEXT
            tok['initially_open'] = False
        elif word == 'стр' and next_surface.isdigit():
            tok['lemma'] = PAGE_ABBR_CONTEXT
            tok['context_reading'] = PAGE_ABBR_CONTEXT
            tok['initially_open'] = False
        elif word == 'ул' and next_surface[:1].isupper():
            tok['lemma'] = STREET_ABBR_CONTEXT
            tok['context_reading'] = STREET_ABBR_CONTEXT
            tok['initially_open'] = False
    return tokens


def build_puzzle_payload(
    *,
    wiki_title: str,
    body_text: str,
    wiki_pageid: int | None = None,
    wiki_revid: int | None = None,
    fetched_at: str | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    """Snapshot the cleaned article and derive tokens with the current splitter.

    ``body_text`` is the source of truth. Tokens are a cache stamped with
    ``splitter_version`` and are rebuilt when that version changes.
    """
    title_tokens = tokenize_text(wiki_title, in_title=True, start_id=0)
    body_tokens = tokenize_text(body_text, in_title=False, start_id=len(title_tokens))
    return {
        'wiki_title': wiki_title,
        'wiki_pageid': wiki_pageid,
        'wiki_revid': wiki_revid,
        'fetched_at': fetched_at,
        'body_text': body_text,
        'title_tokens': title_tokens,
        'body_tokens': body_tokens,
        'truncated': bool(truncated),
        'splitter_version': CENSORLY_SPLITTER_VERSION,
    }


def all_tokens(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload.get('title_tokens') or []) + list(payload.get('body_tokens') or [])


def title_content_lemmas(payload: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for tok in payload.get('title_tokens') or []:
        if tok.get('kind') == 'content' and tok.get('lemma'):
            out.add(tok['lemma'])
    return out


def ordered_title_lemmas(payload: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for tok in payload.get('title_tokens') or []:
        if tok.get('kind') != 'content':
            continue
        lemma = tok.get('lemma') or ''
        if lemma and lemma not in seen:
            seen.append(lemma)
    return seen


def _strip_token_surface(tok: dict[str, Any]) -> dict[str, Any]:
    surface = tok.get('surface') or ''
    out = tok
    if surface and not surface.isspace():
        plain = strip_combining_marks(surface)
        if plain != surface:
            out = dict(tok)
            out['surface'] = plain
    # Drop noisy endings stored before the allowlist filter.
    ending = (out.get('ending') or '').strip()
    if ending:
        from games.puzzles.censorly.text.normalize import is_hintable_ending
        stem = ''
        try:
            stem_len = int(out.get('stem_length') or 0)
        except (TypeError, ValueError):
            stem_len = 0
        if stem_len > 0:
            stem = 'x' * stem_len
        if not is_hintable_ending(ending, stem=stem):
            if out is tok:
                out = dict(tok)
            out.pop('ending', None)
            out.pop('stem_length', None)
    return out


def _expand_legacy_heading(tok: dict[str, Any], start_id: int) -> list[dict[str, Any]]:
    """Turn one kind=heading blob into break + maskable heading words + break."""
    surface = strip_combining_marks((tok.get('surface') or '').strip())
    if not surface:
        return []
    out: list[dict[str, Any]] = [_heading_break(start_id)]
    tid = start_id + 1
    chunk = _tokenize_chunk(surface, in_title=False, start_id=tid, in_heading=True)
    for tok in chunk:
        tok['heading_level'] = 2
    out.extend(chunk)
    tid = tid + len(chunk)
    out.append(_heading_break(tid))
    return out


def upgrade_puzzle_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize stored puzzles: strip accents, expand legacy heading blobs.

    Ids are reassigned sequentially so expand stays consistent. Callers must not
    persist player state keyed only by old heading token ids (hints on headings
    were never allowed for title lemmas; body heading hints are rare).
    """
    if not isinstance(payload, dict):
        return payload
    changed = False
    new_title: list[dict[str, Any]] = []
    new_body: list[dict[str, Any]] = []
    for key, dest in (('title_tokens', new_title), ('body_tokens', new_body)):
        for tok in payload.get(key) or []:
            if not isinstance(tok, dict):
                continue
            if tok.get('kind') == 'heading':
                dest.extend(_expand_legacy_heading(tok, start_id=0))
                changed = True
                continue
            stripped = _strip_token_surface(tok)
            if stripped is not tok:
                changed = True
            if 'in_heading' not in stripped and stripped.get('kind') != 'heading_break':
                stripped = dict(stripped)
                stripped['in_heading'] = False
            dest.append(stripped)
    if not changed and all(
        'in_heading' in t or t.get('kind') == 'heading_break'
        for t in (payload.get('title_tokens') or []) + (payload.get('body_tokens') or [])
        if isinstance(t, dict)
    ):
        return payload
    # Reassign ids so expand stays unique and sequential.
    tid = 0
    for tok in new_title + new_body:
        tok['id'] = tid
        tid += 1
    out = dict(payload)
    out['title_tokens'] = new_title
    out['body_tokens'] = new_body
    return out


def _join_surfaces(tokens: list[dict[str, Any]]) -> str:
    return ''.join((tok.get('surface') or '') for tok in tokens)


def reconstruct_body_text(tokens: list[dict[str, Any]]) -> str:
    """Rebuild cleaned article text from stored tokens.

    Heading breaks become the same marked headings ``tokenize_text`` reads.
    Used when an older puzzle saved tokens and not ``body_text``.
    """
    parts: list[str] = []
    index = 0
    total = len(tokens)
    while index < total:
        tok = tokens[index]
        if tok.get('kind') == 'heading_break':
            index += 1
            chunk: list[dict[str, Any]] = []
            level = 2
            while index < total and tokens[index].get('kind') != 'heading_break':
                chunk.append(tokens[index])
                try:
                    found = int(tokens[index].get('heading_level') or 0)
                except (TypeError, ValueError):
                    found = 0
                if 2 <= found <= 6:
                    level = found
                index += 1
            name = _join_surfaces(chunk).strip()
            if name:
                parts.append(
                    f'{HEADING_START}{level}{HEADING_LEVEL_SEP}{name}{HEADING_END}'
                )
            if index < total and tokens[index].get('kind') == 'heading_break':
                index += 1
            continue
        parts.append(tok.get('surface') or '')
        index += 1
    return ''.join(parts)


def _copy_snapshot_extras(source: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    alias = (source.get('source_pool_title') or '').strip()
    if alias:
        fresh['source_pool_title'] = alias
    return fresh


def resolve_puzzle_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return tokens for the current splitter without refetching Wikipedia.

    The cleaned title and body stay as stored. A missing body is rebuilt from
    the saved tokens. A matching ``splitter_version`` keeps the token cache.
    """
    if not isinstance(payload, dict):
        return payload
    if payload.get('title_tokens') is None and not isinstance(payload.get('body_text'), str):
        return payload
    version_ok = payload.get('splitter_version') == CENSORLY_SPLITTER_VERSION
    has_text = isinstance(payload.get('body_text'), str)
    if version_ok and has_text and payload.get('title_tokens') is not None:
        return upgrade_puzzle_payload(payload)
    if has_text:
        title = payload.get('wiki_title') or ''
        body = payload.get('body_text') or ''
        if not version_ok:
            # Older stored extracts may contain raw == heading == text because
            # whitespace cleanup joined the following paragraph to the heading.
            from games.puzzles.censorly.article.wiki import _headings_to_marked

            body = _headings_to_marked(body)
        source = payload
    else:
        source = upgrade_puzzle_payload(payload)
        title = (source.get('wiki_title') or '').strip()
        if not title:
            title = _join_surfaces(source.get('title_tokens') or []).strip()
        body = reconstruct_body_text(source.get('body_tokens') or [])
    fresh = build_puzzle_payload(
        wiki_title=title,
        body_text=body,
        wiki_pageid=source.get('wiki_pageid'),
        wiki_revid=source.get('wiki_revid'),
        fetched_at=source.get('fetched_at'),
        truncated=bool(source.get('truncated')),
    )
    return _copy_snapshot_extras(source, fresh)
