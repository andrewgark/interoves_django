"""Запрещённые слова (алфавитка) и задания (неделя) для support-консоли."""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from games.alphabetty.core import normalize_word
from games.models import Game

ALPHABETTY_BANNED_WORDS_TAG = 'support_banned_words'
WEEK_TASK_BANNED_UNITS_TAG = 'support_banned_units'


def _now_iso() -> str:
    return timezone.now().isoformat()


def list_banned_words(game: Game) -> list[dict[str, Any]]:
    tags = game.tags or {}
    raw = tags.get(ALPHABETTY_BANNED_WORDS_TAG) or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            word = normalize_word(item)
            if word:
                out.append({'word': word, 'banned_at': ''})
        elif isinstance(item, dict):
            word = normalize_word(str(item.get('word') or ''))
            if word:
                out.append({
                    'word': word,
                    'banned_at': str(item.get('banned_at') or ''),
                })
    out.sort(key=lambda row: row['word'])
    return out


def banned_word_set(game: Game) -> set[str]:
    return {row['word'] for row in list_banned_words(game)}


def add_banned_word(game: Game, word: str) -> list[dict[str, Any]]:
    word_n = normalize_word(word)
    if not word_n:
        return list_banned_words(game)
    rows = [r for r in list_banned_words(game) if r['word'] != word_n]
    rows.append({'word': word_n, 'banned_at': _now_iso()})
    rows.sort(key=lambda row: row['word'])
    tags = dict(game.tags or {})
    tags[ALPHABETTY_BANNED_WORDS_TAG] = rows
    game.tags = tags
    game.save(update_fields=['tags'])
    return rows


def remove_banned_word(game: Game, word: str) -> list[dict[str, Any]]:
    word_n = normalize_word(word)
    rows = [r for r in list_banned_words(game) if r['word'] != word_n]
    tags = dict(game.tags or {})
    if rows:
        tags[ALPHABETTY_BANNED_WORDS_TAG] = rows
    else:
        tags.pop(ALPHABETTY_BANNED_WORDS_TAG, None)
    game.tags = tags
    game.save(update_fields=['tags'])
    return rows


def _unit_key(task_group_id: int, task_numbers: list[str] | None) -> tuple[int, frozenset[str] | None]:
    nums = None if task_numbers is None else frozenset(str(x) for x in task_numbers)
    return (int(task_group_id), nums)


def _unit_from_raw(item: dict[str, Any]) -> dict[str, Any] | None:
    try:
        tg_id = int(item.get('task_group_id'))
    except (TypeError, ValueError):
        return None
    nums_raw = item.get('task_numbers')
    if nums_raw is None:
        task_numbers = None
    elif isinstance(nums_raw, (list, tuple)):
        task_numbers = [str(x) for x in nums_raw]
    else:
        return None
    return {
        'task_group_id': tg_id,
        'task_numbers': task_numbers,
        'label': str(item.get('label') or ''),
        'banned_at': str(item.get('banned_at') or ''),
    }


def list_banned_units(game: Game) -> list[dict[str, Any]]:
    tags = game.tags or {}
    raw = tags.get(WEEK_TASK_BANNED_UNITS_TAG) or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, frozenset[str] | None]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = _unit_from_raw(item)
        if row is None:
            continue
        key = _unit_key(row['task_group_id'], row['task_numbers'])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    out.sort(key=lambda row: (row['label'] or '', row['task_group_id']))
    return out


def banned_unit_keys(game: Game) -> set[tuple[int, frozenset[str] | None]]:
    keys: set[tuple[int, frozenset[str] | None]] = set()
    for row in list_banned_units(game):
        keys.add(_unit_key(row['task_group_id'], row['task_numbers']))
    return keys


def add_banned_unit(
    game: Game,
    *,
    task_group_id: int,
    task_numbers: list[str] | None,
    label: str = '',
) -> list[dict[str, Any]]:
    key = _unit_key(task_group_id, task_numbers)
    rows = [
        r for r in list_banned_units(game)
        if _unit_key(r['task_group_id'], r['task_numbers']) != key
    ]
    rows.append({
        'task_group_id': key[0],
        'task_numbers': list(key[1]) if key[1] is not None else None,
        'label': (label or '').strip(),
        'banned_at': _now_iso(),
    })
    rows.sort(key=lambda row: (row['label'] or '', row['task_group_id']))
    tags = dict(game.tags or {})
    tags[WEEK_TASK_BANNED_UNITS_TAG] = rows
    game.tags = tags
    game.save(update_fields=['tags'])
    return rows


def remove_banned_unit(
    game: Game,
    *,
    task_group_id: int,
    task_numbers: list[str] | None,
) -> list[dict[str, Any]]:
    key = _unit_key(task_group_id, task_numbers)
    rows = [
        r for r in list_banned_units(game)
        if _unit_key(r['task_group_id'], r['task_numbers']) != key
    ]
    tags = dict(game.tags or {})
    if rows:
        tags[WEEK_TASK_BANNED_UNITS_TAG] = rows
    else:
        tags.pop(WEEK_TASK_BANNED_UNITS_TAG, None)
    game.tags = tags
    game.save(update_fields=['tags'])
    return rows
