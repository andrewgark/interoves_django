"""Словари Алфавитки (easy∪hard = загадки, valid = допустимые отгадки)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

DICT_DIR = Path(__file__).resolve().parent / 'dictionaries'


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


@lru_cache(maxsize=1)
def load_valid_set() -> frozenset[str]:
    from games.alphabetty.core import normalize_word

    raw = _read_words(DICT_DIR / 'ru_nouns_valid.txt')
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
