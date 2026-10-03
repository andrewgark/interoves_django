"""Normalize and lemmatize Russian words for Цензурки."""

from __future__ import annotations

import re
import unicodedata

from games.matcher.norm_matcher import get_norm_form

_WORD_RE = re.compile(r'[A-Za-zА-Яа-яЁё0-9]+', re.UNICODE)

# Endings come from the lemma/surface common-prefix split. Keep structural
# guards only — 1-letter tails (а/е/и/л/…) and short leftovers (ек, ми) are OK
# when they are the true inflectional remainder.
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
    """True if ending looks like a real inflectional remainder after the stem."""
    e = normalize_surface(ending)
    if not e or len(e) > 8 or not _ENDING_RE.fullmatch(e):
        return False
    if stem:
        s = normalize_surface(stem)
        # Stem must dominate so the black bar still hides the word.
        if len(s) < 2 or len(e) >= len(s):
            return False
    return True


def split_stem_ending(surface: str) -> tuple[str, str]:
    """Split a word into (stem, ending) via common prefix with its lemma.

    Ending is lowercase without accents (including 1-letter tails). Empty ending
    means show a solid mask.
    """
    plain = normalize_surface(surface)
    if not plain or plain.isdigit() or '-' in plain:
        return plain, ''
    lemma = lemma_of(plain)
    if not lemma or lemma == plain:
        return plain, ''
    i = 0
    limit = min(len(plain), len(lemma))
    while i < limit and plain[i] == lemma[i]:
        i += 1
    # Keep a real stem; skip tiny leftovers that would leak most of the word.
    if i < 2:
        return plain, ''
    ending = plain[i:]
    stem = plain[:i]
    if not is_hintable_ending(ending, stem=stem):
        return plain, ''
    return stem, ending


def is_guessable_word(word: str) -> bool:
    """One token: Cyrillic and/or Latin letters, digits, hyphens."""
    n = normalize_surface(word)
    if not n:
        return False
    return bool(re.fullmatch(r'[a-zа-я0-9\-]+', n))


# Back-compat alias used by older call sites / tests.
def is_cyrillic_word(word: str) -> bool:
    return is_guessable_word(word)
