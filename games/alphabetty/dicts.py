"""Словари Алфавитки (easy∪hard = загадки, valid = допустимые отгадки)."""

from __future__ import annotations

import gzip
from functools import lru_cache
from pathlib import Path
from typing import Optional

DICT_DIR = Path(__file__).resolve().parent / 'dictionaries'
# Полный словарь форм (не только им.п. сущ.): gzip, одна словоформа на строку.
_VALID_GZ = DICT_DIR / 'ru_words_valid.txt.gz'
# Запасной плоский список (если gzip ещё не собран) + старый nouns-only.
_VALID_FALLBACKS = (
    DICT_DIR / 'ru_words_valid.txt',
    DICT_DIR / 'ru_nouns_valid.txt',
)

# Одобренные через админку слова (поверх файла). Сбрасывается invalidate_approved_extras().
_approved_extras: Optional[frozenset[str]] = None


def _read_words(path: Path) -> list[str]:
    if not path.is_file():
        return []
    words: list[str] = []
    with path.open('r', encoding='utf-8') as f:
        for line in f:
            w = line.strip()
            if w:
                words.append(w)
    return words


def _read_valid_raw() -> list[str]:
    if _VALID_GZ.is_file():
        words: list[str] = []
        with gzip.open(_VALID_GZ, 'rt', encoding='utf-8') as f:
            for line in f:
                w = line.strip()
                if w:
                    words.append(w)
        return words
    for path in _VALID_FALLBACKS:
        raw = _read_words(path)
        if raw:
            return raw
    return []


@lru_cache(maxsize=1)
def load_valid_set() -> frozenset[str]:
    from games.alphabetty.core import normalize_word

    raw = _read_valid_raw()
    return frozenset(normalize_word(w) for w in raw if normalize_word(w))


@lru_cache(maxsize=1)
def load_answer_pool() -> tuple[str, ...]:
    """Уникальные загадки: easy ∪ hard, в стабильном порядке."""
    from games.alphabetty.core import normalize_word

    easy = _read_words(DICT_DIR / 'ru_nouns_easy.txt')
    hard = _read_words(DICT_DIR / 'ru_nouns_hard.txt')
    seen: set[str] = set()
    ordered: list[str] = []
    for w in easy + hard:
        n = normalize_word(w)
        if not n or n in seen:
            continue
        seen.add(n)
        ordered.append(n)
    return tuple(ordered)


def get_valid_set() -> frozenset[str]:
    return load_valid_set()


def get_answer_pool() -> tuple[str, ...]:
    return load_answer_pool()


def invalidate_approved_extras() -> None:
    global _approved_extras
    _approved_extras = None


def get_approved_extras() -> frozenset[str]:
    """Слова со статусом Approved из админки."""
    global _approved_extras
    if _approved_extras is not None:
        return _approved_extras
    try:
        from games.models import AlphabettyDictSuggestion
    except Exception:
        _approved_extras = frozenset()
        return _approved_extras
    _approved_extras = frozenset(
        AlphabettyDictSuggestion.objects.filter(
            status=AlphabettyDictSuggestion.STATUS_APPROVED,
        ).values_list('word', flat=True)
    )
    return _approved_extras


def is_approved_dict_word(word: str) -> bool:
    return word in get_approved_extras()
