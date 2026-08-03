"""Игровой прогресс Алфавитки: состояние, попытки, ответ API."""

from __future__ import annotations

import json
from typing import Any, Optional

from django.db import transaction
from django.db.models import Min, Max

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


def ru_attempt_word(n: int) -> str:
    n = abs(int(n))
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return 'попытка'
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return 'попытки'
    return 'попыток'


def format_elapsed(seconds: int | None) -> str:
    """1ч 32м 44с / 32м 44с / 44с."""
    if seconds is None:
        seconds = 0
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f'{h}ч {m}м {s}с'
    if m:
        return f'{m}м {s}с'
    return f'{s}с'


def elapsed_seconds_for_actor(*, game: Game, task: Task, actor: dict) -> int:
    """Время от первой до последней валидной попытки актёра."""
    agg = (
        Attempt.manager.filter(task=task, game=game, **actor)
        .exclude(time__isnull=True)
        .aggregate(t0=Min('time'), t1=Max('time'))
    )
    t0, t1 = agg.get('t0'), agg.get('t1')
    if t0 is None or t1 is None:
        return 0
    return max(0, int((t1 - t0).total_seconds()))


def build_share_lines(
    *,
    number: int,
    attempts: int,
    elapsed_seconds: int,
    host: str = 'interoves.com',
) -> list[str]:
    host = (host or 'interoves.com').split(':')[0] or 'interoves.com'
    return [
        f'🔤 Алфавитка #{int(number)}',
        f'🤔 {attempts} {ru_attempt_word(attempts)}',
        f'⏱️ {format_elapsed(elapsed_seconds)}',
        f'🔗 {host}/alphabetty/{int(number)}',
    ]


def attach_solve_meta(
    payload: dict[str, Any],
    *,
    game: Game,
    task: Task,
    number: int | str,
    actor: Optional[dict],
    host: str = 'interoves.com',
) -> dict[str, Any]:
    """Добавить время/share-текст для решённой алфавитки."""
    if not payload.get('won'):
        return payload
    try:
        num = int(number)
    except (TypeError, ValueError):
        num = 0
    attempts = int(payload.get('attempts') or 0)
    elapsed = elapsed_seconds_for_actor(game=game, task=task, actor=actor) if actor else 0
    lines = build_share_lines(
        number=num,
        attempts=attempts,
        elapsed_seconds=elapsed,
        host=host,
    )
    payload['elapsed_seconds'] = elapsed
    payload['elapsed_label'] = format_elapsed(elapsed)
    payload['attempts_label'] = f'{attempts} {ru_attempt_word(attempts)}'
    payload['share_lines'] = lines
    payload['share_text'] = '\n'.join(lines)
    return payload


def hub_progress_for_actor(
    *,
    game: Game,
    numbers_and_tasks: list[tuple[int, Task]],
    user=None,
    anon_key=None,
) -> dict[int, dict[str, Any]]:
    """Прогресс для списка алфавиток: won / attempts / elapsed / meta."""
    actor = _actor_filters(user=user, anon_key=anon_key)
    out: dict[int, dict[str, Any]] = {}
    if actor is None or not numbers_and_tasks:
        return out
    task_ids = [t.id for _, t in numbers_and_tasks]
    states = {
        row.task_id: load_state(row.state)
        for row in ChainTaskState.objects.filter(
            game=game,
            game_mode='general',
            task_id__in=task_ids,
            **actor,
        )
    }
    # Время по всем task_id одним запросом.
    timing_rows = (
        Attempt.manager.filter(task_id__in=task_ids, game=game, **actor)
        .exclude(time__isnull=True)
        .values('task_id')
        .annotate(t0=Min('time'), t1=Max('time'))
    )
    timing = {
        r['task_id']: max(0, int((r['t1'] - r['t0']).total_seconds()))
        for r in timing_rows
        if r['t0'] is not None and r['t1'] is not None
    }
    for number, task in numbers_and_tasks:
        state = states.get(task.id) or default_state()
        won = bool(state.get('won'))
        attempts = len(state.get('guesses') or [])
        if not won and not attempts:
            continue
        elapsed = timing.get(task.id, 0) if won else None
        meta = None
        if won:
            meta = f'🤔 {attempts} {ru_attempt_word(attempts)}  ⏱️ {format_elapsed(elapsed or 0)}'
        elif attempts:
            meta = f'🤔 {attempts} {ru_attempt_word(attempts)}'
        out[number] = {
            'won': won,
            'attempts': attempts,
            'elapsed_seconds': elapsed,
            'progress_meta': meta,
            'row_class': 'new-task--solved' if won else 'new-task--partial',
            'is_solved': won,
        }
    return out


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
    number: int | str | None = None,
    share_host: str = 'interoves.com',
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
    num = number if number is not None else task.task_group_id

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
        return attach_solve_meta(
            payload, game=game, task=task, number=num, actor=actor, host=share_host,
        )

    if not is_valid_guess(normalized, user=user, anon_key=anon_key):
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
    if status == 'correct':
        return attach_solve_meta(
            payload, game=game, task=task, number=num, actor=actor, host=share_host,
        )
    return payload


def get_play_state(
    *,
    game: Game,
    task: Task,
    user=None,
    anon_key=None,
    number: int | str | None = None,
    share_host: str = 'interoves.com',
) -> dict[str, Any]:
    """Прочитать прогресс. Не создаёт пустой ChainTaskState (иначе anon-migrate
    может проиграть merge пустому state от простого открытия страницы)."""
    actor = _actor_filters(user=user, anon_key=anon_key)
    secret = secret_from_task(task)
    num = number if number is not None else 0
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
    payload = public_payload(load_state(row.state), secret)
    return attach_solve_meta(
        payload, game=game, task=task, number=num, actor=actor, host=share_host,
    )
