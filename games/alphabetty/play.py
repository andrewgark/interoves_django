"""Игровой прогресс Алфавитки: состояние, попытки, ответ API."""

from __future__ import annotations

import json
from typing import Any

from django.db import transaction

from games.alphabetty.core import (
    build_prefix_level,
    guess_status,
    is_valid_guess,
    max_word_length,
    normalize_word,
)
from games.models import Attempt, ChainTaskState, Game, GameTaskGroup, Task


def default_state() -> dict[str, Any]:
    return {'guesses': [], 'won': False}


def load_state(raw: str | None) -> dict[str, Any]:
    state = default_state()
    if not raw:
        return state
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return state
    if not isinstance(data, dict):
        return state
    guesses = data.get('guesses') or []
    if isinstance(guesses, list):
        state['guesses'] = [normalize_word(g) for g in guesses if normalize_word(g)]
    state['won'] = bool(data.get('won'))
    return state


def dump_state(state: dict[str, Any]) -> str:
    return json.dumps({
        'guesses': list(state.get('guesses') or []),
        'won': bool(state.get('won')),
    }, ensure_ascii=False)


def secret_from_task(task: Task) -> str:
    return normalize_word((task.answer or '').strip().splitlines()[0] if task.answer else '')


def split_ladder(guesses: list[str], secret: str) -> tuple[list[str], list[str]]:
    """Слова раньше секрета (по возрастанию) и позже (по возрастанию)."""
    from games.alphabetty.core import alphabet_key, compare_words

    earlier = sorted(
        [g for g in guesses if compare_words(g, secret) < 0],
        key=alphabet_key,
    )
    later = sorted(
        [g for g in guesses if compare_words(g, secret) > 0],
        key=alphabet_key,
    )
    return earlier, later


def public_payload(
    state: dict[str, Any],
    secret: str,
    *,
    reveal_secret: bool = False,
) -> dict[str, Any]:
    guesses = list(state.get('guesses') or [])
    won = bool(state.get('won')) or (normalize_word(secret) in guesses)
    earlier, later = split_ladder(guesses, secret)
    lo = earlier[-1] if earlier else None
    hi = later[0] if later else None
    prefix_hint = build_prefix_level(lo, hi) if (lo or hi) and not won else []
    payload = {
        'guesses': guesses,
        'earlier': earlier,
        'later': later,
        'bounds': {'lo': lo, 'hi': hi},
        'prefix_hint': prefix_hint,
        'won': won,
        'attempts': len(guesses),
        'max_word_length': max_word_length(),
    }
    if won or reveal_secret:
        payload['secret'] = normalize_word(secret)
    return payload


def get_task_for_number(game: Game, number: int | str) -> tuple[GameTaskGroup, Task]:
    link = (
        GameTaskGroup.objects.filter(game=game, number=str(number))
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise LookupError('puzzle_not_found')
    task = Task.objects.filter(task_group_id=link.task_group_id, number='1').first()
    if task is None:
        raise LookupError('task_not_found')
    return link, task


def _actor_filters(user=None, anon_key=None):
    if user is not None and getattr(user, 'is_authenticated', False):
        return {'user': user, 'team': None, 'anon_key': None}
    if anon_key:
        return {'user': None, 'team': None, 'anon_key': str(anon_key)}
    return None


@transaction.atomic
def apply_guess(
    *,
    game: Game,
    task: Task,
    word: str,
    user=None,
    anon_key=None,
) -> dict[str, Any]:
    """Применить отгадку; вернуть публичный payload + status."""
    actor = _actor_filters(user=user, anon_key=anon_key)
    if actor is None:
        return {
            'status': 'error',
            'error': 'Нужен пользователь или anon_key',
            **public_payload(default_state(), secret_from_task(task)),
        }

    secret = secret_from_task(task)
    if not secret:
        return {'status': 'error', 'error': 'Загадка не настроена'}

    normalized = normalize_word(word)

    ChainTaskState.objects.get_or_create(
        task=task,
        game=game,
        game_mode='general',
        defaults={'state': dump_state(default_state())},
        **actor,
    )
    row = ChainTaskState.objects.select_for_update().get(
        task=task,
        game=game,
        game_mode='general',
        **actor,
    )
    state = load_state(row.state)

    if state['won']:
        payload = public_payload(state, secret)
        payload['status'] = 'correct'
        return payload

    if not is_valid_guess(normalized):
        payload = public_payload(state, secret)
        payload['status'] = 'invalid'
        payload['error'] = 'Слова нет в словаре'
        return payload

    if normalized in state['guesses']:
        payload = public_payload(state, secret)
        payload['status'] = 'duplicate'
        payload['error'] = 'Это слово уже вводили'
        return payload

    status = guess_status(normalized, secret)
    state['guesses'].append(normalized)
    if status == 'correct':
        state['won'] = True

    attempt = Attempt(
        task=task,
        game=game,
        text=normalized,
        status='Ok' if status == 'correct' else 'Partial',
        points=1 if status == 'correct' else 0,
        state=dump_state(state),
        **actor,
    )
    attempt.save()

    row.state = dump_state(state)
    row.last_attempt = attempt
    row.save(update_fields=['state', 'last_attempt', 'updated_at'])

    payload = public_payload(state, secret)
    payload['status'] = status
    return payload


def _get_or_create_state(game, task, actor) -> dict[str, Any]:
    row, _ = ChainTaskState.objects.get_or_create(
        task=task,
        game=game,
        game_mode='general',
        defaults={'state': dump_state(default_state())},
        **actor,
    )
    return load_state(row.state)


def get_play_state(*, game: Game, task: Task, user=None, anon_key=None) -> dict[str, Any]:
    """Прочитать прогресс. Не создаёт пустой ChainTaskState (иначе anon-migrate
    может проиграть merge пустому state от простого открытия страницы)."""
    actor = _actor_filters(user=user, anon_key=anon_key)
    secret = secret_from_task(task)
    if actor is None:
        return public_payload(default_state(), secret)
    row = ChainTaskState.objects.filter(
        task=task,
        game=game,
        game_mode='general',
        **actor,
    ).first()
    if row is None:
        return public_payload(default_state(), secret)
    return public_payload(load_state(row.state), secret)
