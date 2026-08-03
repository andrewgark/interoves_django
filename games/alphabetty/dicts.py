"""Словари Алфавитки (easy∪hard∪ApprovedAnswer = загадки, valid = допустимые отгадки)."""

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

# Одобренные через админку слова (поверх файла). Сбрасывается invalidate_dict_caches().
_approved_extras: Optional[frozenset[str]] = None
_answer_extras: Optional[frozenset[str]] = None


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
    """Уникальные загадки из файлов: easy ∪ hard, в стабильном порядке."""
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


def invalidate_approved_extras() -> None:
    """Совместимое имя: сбросить кэши одобренных extras (valid + answer)."""
    invalidate_dict_caches()


def invalidate_dict_caches() -> None:
    global _approved_extras, _answer_extras
    _approved_extras = None
    _answer_extras = None


def get_approved_extras() -> frozenset[str]:
    """Слова со статусом Approved / ApprovedAnswer из админки (глобально валидные)."""
    global _approved_extras
    if _approved_extras is not None:
        return _approved_extras
    try:
        from games.models import AlphabettyDictSuggestion

        words = frozenset(
            AlphabettyDictSuggestion.objects.filter(
                status__in=AlphabettyDictSuggestion.STATUSES_VALID,
            ).values_list('word', flat=True)
        )
    except Exception:
        # Таблица может ещё не существовать во время ранних миграций — не кэшируем.
        return frozenset()
    _approved_extras = words
    return _approved_extras


def get_answer_extras() -> frozenset[str]:
    """Слова со статусом ApprovedAnswer — доп. пул для загадывания."""
    global _answer_extras
    if _answer_extras is not None:
        return _answer_extras
    try:
        from games.models import AlphabettyDictSuggestion

        words = frozenset(
            AlphabettyDictSuggestion.objects.filter(
                status=AlphabettyDictSuggestion.STATUS_APPROVED_ANSWER,
            ).values_list('word', flat=True)
        )
    except Exception:
        return frozenset()
    _answer_extras = words
    return _answer_extras


def get_answer_pool() -> tuple[str, ...]:
    """Файловый пул + слова, одобренные для загадывания."""
    base = load_answer_pool()
    extras = get_answer_extras()
    if not extras:
        return base
    seen = set(base)
    ordered = list(base)
    for w in sorted(extras):
        if w not in seen:
            seen.add(w)
            ordered.append(w)
    return tuple(ordered)


def is_approved_dict_word(word: str) -> bool:
    return word in get_approved_extras()


def is_personal_dict_word(
    word: str,
    *,
    user=None,
    anon_key: str | None = None,
) -> bool:
    """Слово есть в личном словаре актёра."""
    from games.alphabetty.core import normalize_word
    from games.models import AlphabettyPersonalDictWord

    n = normalize_word(word)
    if not n:
        return False
    if user is not None and getattr(user, 'is_authenticated', False):
        return AlphabettyPersonalDictWord.objects.filter(user=user, word=n).exists()
    if anon_key:
        return AlphabettyPersonalDictWord.objects.filter(
            anon_key=str(anon_key), user__isnull=True, word=n,
        ).exists()
    return False


def add_personal_dict_word(
    word: str,
    *,
    user=None,
    anon_key: str | None = None,
) -> bool:
    """Добавить слово в личный словарь. True если создана новая запись."""
    from games.alphabetty.core import normalize_word
    from games.models import AlphabettyPersonalDictWord

    n = normalize_word(word)
    if not n:
        return False
    if user is not None and getattr(user, 'is_authenticated', False):
        _, created = AlphabettyPersonalDictWord.objects.get_or_create(
            user=user,
            word=n,
            defaults={'anon_key': None},
        )
        return created
    if anon_key:
        _, created = AlphabettyPersonalDictWord.objects.get_or_create(
            anon_key=str(anon_key),
            word=n,
            defaults={'user': None},
        )
        return created
    return False
