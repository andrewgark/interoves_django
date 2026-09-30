"""Normalize and lemmatize Russian words for Цензурки."""

from __future__ import annotations

import re

from games.matcher.norm_matcher import get_norm_form

_WORD_RE = re.compile(r'[A-Za-zА-Яа-яЁё0-9]+', re.UNICODE)


def normalize_surface(text: str) -> str:
    """Lowercase, ё→е, strip; keep only letters/digits for matching."""
    s = (text or '').strip().lower().replace('ё', 'е').replace('Ё', 'е')
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


def is_cyrillic_word(word: str) -> bool:
    n = normalize_surface(word)
    if not n:
        return False
    return bool(re.fullmatch(r'[а-я0-9\-]+', n))
