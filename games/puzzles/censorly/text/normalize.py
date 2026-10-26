"""Normalize and lemmatize Russian words for Цензурки."""

from __future__ import annotations

import re
import unicodedata

from games.puzzles.core.norm_matcher import get_norm_form

_WORD_RE = re.compile(r'[A-Za-zА-Яа-яЁё0-9]+', re.UNICODE)
_CYRILLIC_WORD_RE = re.compile(r'^[а-я]+$', re.UNICODE)

# Product filter for a tail that grammatical_split already judged inflectional.
_ENDING_RE = re.compile(r'^[a-zа-я]+$', re.UNICODE)


def strip_combining_marks(text: str) -> str:
    """Drop Mn marks (e.g. combining acute) after NFC."""
    return ''.join(
        ch for ch in unicodedata.normalize('NFC', text or '')
        if unicodedata.category(ch) != 'Mn'
    )


def normalize_surface(text: str) -> str:
    """Lowercase, ё→е, strip accents; keep letters/digits/hyphens for matching."""
    s = strip_combining_marks(text or '').strip().lower().replace('ё', 'е').replace('Ё', 'е')
    return s


def lemma_of(word: str) -> str:
    """Lemma via pymorphy3; empty string if input empty."""
    n = normalize_surface(word)
    if not n:
        return ''
    try:
        return normalize_surface(get_norm_form(n))
    except Exception:
        return n


def is_hintable_ending(ending: str, *, stem: str = '') -> bool:
    """True if a grammatical tail is short enough to show inside the mask."""
    e = normalize_surface(ending)
    if not e or len(e) > 8 or not _ENDING_RE.fullmatch(e):
        return False
    if stem:
        s = normalize_surface(stem)
        # Two letters must stay under the bar. The tail may be as long as
        # the stem (нового, является) or longer (кажется).
        if len(s) < 2:
            return False
    return True


def split_stem_ending(surface: str) -> tuple[str, str]:
    """Split a word into (stem, grammatical ending).

    The ending is the inflectional tail from ``grammatical_split``, plus a
    reflexive postfix when the form has one. A zero ending and any tail the
    hint filter rejects come back as ``(normalized_word, '')``.
    """
    from games.censorly.endings import grammatical_split

    plain = normalize_surface(surface)
    if not plain or '-' in plain or any(ch.isdigit() for ch in plain):
        return plain, ''
    if not _CYRILLIC_WORD_RE.fullmatch(plain):
        return plain, ''
    try:
        stem, ending = grammatical_split(plain)
    except Exception:
        return plain, ''
    if not ending or not is_hintable_ending(ending, stem=stem):
        return plain, ''
    return stem, ending


# Keep the established Redactle ranges, and include letter characters from
# supplementary Unicode planes (historic and less common scripts).
def _supplementary_letter_ranges() -> str:
    ranges: list[tuple[int, int]] = []
    start = previous = None
    for codepoint in range(0x10000, 0x110000):
        if unicodedata.category(chr(codepoint)).startswith('L'):
            if start is None:
                start = codepoint
            previous = codepoint
        elif start is not None:
            ranges.append((start, previous))
            start = previous = None
    if start is not None:
        ranges.append((start, previous))
    return ''.join(
        f'\\U{first:08X}' if first == last
        else f'\\U{first:08X}-\\U{last:08X}'
        for first, last in ranges
    )


WORD_CHARS = 'A-Za-z0-9_\u00BF-\u1FFF\u2C00-\uD7FF' + _supplementary_letter_ranges()
_WORD_RE = re.compile(f'^[{WORD_CHARS}]+$')


def is_guessable_word(word: str) -> bool:
    """One token in the redactle unicode span. Hyphen and ²/₂ stay outside."""
    n = normalize_surface(word)
    if not n:
        return False
    return bool(_WORD_RE.fullmatch(n))


# Back-compat alias used by older call sites / tests.
def is_cyrillic_word(word: str) -> bool:
    return is_guessable_word(word)
