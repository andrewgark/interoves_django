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
    known_prefix,
    max_word_length,
    normalize_word,
)
from games.models import Attempt, ChainTaskState, Game, GameTaskGroup, Task

# Базовые очки за угаданное слово; каждая буквенная подсказка −1.
ALPHABETTY_BASE_POINTS = 10
ALPHABETTY_HINT_PENALTY = 1


def default_state() -> dict[str, Any]:
    return {'guesses': [], 'won': False, 'hint_prefix': '', 'hints_taken': 0}


def hint_count(state: dict[str, Any]) -> int:
    taken = state.get('hints_taken')
    if taken is not None:
        try:
            return max(0, int(taken))
        except (TypeError, ValueError):
            pass
    # Старые сохранения без hints_taken: одна подсказка = одна буква с начала.
    return len(normalize_word(state.get('hint_prefix') or ''))


def alphabetty_base_points(task: Optional[Task] = None):
    """Максимум баллов за алфавитку (обычно 10)."""
    from decimal import Decimal
    if task is not None:
        try:
            p = task.get_points()
            if p is not None:
                return Decimal(str(p))
        except Exception:
            pass
    return Decimal(ALPHABETTY_BASE_POINTS)


def alphabetty_hint_penalty_points(hints: int) -> int:
    return max(0, int(hints or 0)) * ALPHABETTY_HINT_PENALTY


def letter_hint_penalty_for_actor(*, game, task, user=None, anon_key=None, team=None) -> int:
    """Штраф за буквенные подсказки из ChainTaskState (−1 за каждую)."""
    if task is None or getattr(task, 'task_type', None) != 'alphabetty':
        return 0
    if game is None:
        return 0
    qs = ChainTaskState.objects.filter(task=task, game=game, game_mode='general')
    if team is not None:
        qs = qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
    elif user is not None:
        qs = qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
    elif anon_key:
        qs = qs.filter(anon_key=str(anon_key), team__isnull=True, user__isnull=True)
    else:
        return 0
    row = qs.first()
    if row is None:
        return 0
    return alphabetty_hint_penalty_points(hint_count(load_state(row.state)))


def ru_hint_word(n: int) -> str:
    n = abs(int(n))
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return 'подсказка'
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return 'подсказки'
    return 'подсказок'


def ru_hint_taken(n: int) -> str:
    n = abs(int(n))
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return 'Взята'
    return 'Взято'


def format_hints_label(count: int) -> str:
    """💡💡 Взято N подсказки — для share и списка."""
    n = max(0, int(count))
    if n <= 0:
        return ''
    return f'{"💡" * n} {ru_hint_taken(n)} {n} {ru_hint_word(n)}'


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
    hints: int = 0,
    host: str = 'interoves.com',
) -> list[str]:
    host = (host or 'interoves.com').split(':')[0] or 'interoves.com'
    lines = [
        f'🔤 Алфавитка #{int(number)}',
        f'🤔 {attempts} {ru_attempt_word(attempts)}',
    ]
    hints_line = format_hints_label(hints)
    if hints_line:
        lines.append(hints_line)
    lines.extend([
        f'⏱️ {format_elapsed(elapsed_seconds)}',
        f'🔗 {host}/alphabetty/{int(number)}',
    ])
    return lines


def attach_solve_meta(
    payload: dict[str, Any],
    *,
    game: Game,
    task: Task,
    number: int | str,
    actor: Optional[dict],
    host: str = 'interoves.com',
) -> dict[str, Any]:
    """Добавить attempts_label; для решённой — ещё время/share-текст."""
    attempts = int(payload.get('attempts') or 0)
    payload['attempts_label'] = f'{attempts} {ru_attempt_word(attempts)}'
    if not payload.get('won'):
        return payload
    try:
        num = int(number)
    except (TypeError, ValueError):
        num = 0
    hints = int(payload.get('hints') or 0)
    elapsed = elapsed_seconds_for_actor(game=game, task=task, actor=actor) if actor else 0
    lines = build_share_lines(
        number=num,
        attempts=attempts,
        elapsed_seconds=elapsed,
        hints=hints,
        host=host,
    )
    payload['elapsed_seconds'] = elapsed
    payload['elapsed_label'] = format_elapsed(elapsed)
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
        hints = hint_count(state)
        if not won and not attempts and not hints:
            continue
        elapsed = timing.get(task.id, 0) if won else None
        meta = None
        if won:
            meta = f'🤔 {attempts} {ru_attempt_word(attempts)}  ⏱️ {format_elapsed(elapsed or 0)}'
            hints_line = format_hints_label(hints)
            if hints_line:
                meta += f'  {hints_line}'
        elif attempts or hints:
            parts = []
            if attempts:
                parts.append(f'🤔 {attempts} {ru_attempt_word(attempts)}')
            hints_line = format_hints_label(hints)
            if hints_line:
                parts.append(hints_line)
            meta = '  '.join(parts)
        out[number] = {
            'won': won,
            'attempts': attempts,
            'hints': hints,
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
    state['hint_prefix'] = normalize_word(data.get('hint_prefix') or '')
    try:
        state['hints_taken'] = max(0, int(data.get('hints_taken') or 0))
    except (TypeError, ValueError):
        state['hints_taken'] = 0
    return state


def dump_state(state: dict[str, Any]) -> str:
    return json.dumps({
        'guesses': list(state.get('guesses') or []),
        'won': bool(state.get('won')),
        'hint_prefix': normalize_word(state.get('hint_prefix') or ''),
        'hints_taken': hint_count(state),
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
    hp = normalize_word(state.get('hint_prefix') or '')
    kp = known_prefix(lo, hi, hint_prefix=hp)
    secret_n = normalize_word(secret)
    prefix_expand = kp if kp else ''
    prefix_hint = (
        build_prefix_level(lo, hi, expand_prefix=prefix_expand)
        if (lo or hi or prefix_expand) and not won
        else []
    )
    next_hint_letter = len(kp) + 1 if not won and len(kp) < len(secret_n) else None
    payload = {
        'guesses': guesses,
        'earlier': earlier,
        'later': later,
        'bounds': {'lo': lo, 'hi': hi},
        'known_prefix': kp,
        'hint_prefix': hp,
        'hints': hint_count(state),
        'next_hint_letter': next_hint_letter,
        'prefix_hint': prefix_hint,
        'won': won,
        'attempts': len(guesses),
        'attempts_label': f'{len(guesses)} {ru_attempt_word(len(guesses))}',
        'max_word_length': max_word_length(),
    }
    if won or reveal_secret:
        payload['secret'] = normalize_word(secret)
    return payload


@transaction.atomic
def apply_hint(
    *,
    game: Game,
    task: Task,
    user=None,
    anon_key=None,
    number: int | str | None = None,
    share_host: str = 'interoves.com',
) -> dict[str, Any]:
    """Раскрыть следующую букву загаданного слова."""
    actor = _actor_filters(user=user, anon_key=anon_key)
    secret = secret_from_task(task)
    if not secret:
        return {'status': 'error', 'error': 'Загадка не настроена'}
    if actor is None:
        return {
            'status': 'error',
            'error': 'Нужен пользователь или anon_key',
            **public_payload(default_state(), secret),
        }

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
        payload['status'] = 'already_won'
        return attach_solve_meta(
            payload, game=game, task=task, number=num, actor=actor, host=share_host,
        )

    hp = normalize_word(state.get('hint_prefix') or '')
    guesses = list(state.get('guesses') or [])
    earlier, later = split_ladder(guesses, secret)
    lo = earlier[-1] if earlier else None
    hi = later[0] if later else None
    kp = known_prefix(lo, hi, hint_prefix=hp)
    pos = len(kp)
    if pos >= len(secret):
        payload = public_payload(state, secret)
        payload['status'] = 'no_more_hints'
        payload['error'] = 'Все буквы уже раскрыты'
        return payload

    revealed = secret[pos]
    state['hint_prefix'] = kp + revealed
    state['hints_taken'] = hint_count(state) + 1
    row.state = dump_state(state)
    row.save(update_fields=['state', 'updated_at'])

    payload = public_payload(state, secret)
    payload['status'] = 'ok'
    payload['hint_reveal'] = {
        'position': pos + 1,
        'letter': revealed,
        'prefix': kp + revealed,
    }
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


def _read_actor_state(*, game: Game, task: Task, actor: dict) -> dict[str, Any]:
    """Read ChainTaskState without creating a row or taking a lock."""
    row = ChainTaskState.objects.filter(
        task=task,
        game=game,
        game_mode='general',
        **actor,
    ).first()
    if row is None:
        return default_state()
    return load_state(row.state)


@transaction.atomic
def _commit_guess(
    *,
    game: Game,
    task: Task,
    normalized: str,
    secret: str,
    actor: dict,
    num: int | str,
    share_host: str,
) -> dict[str, Any]:
    """Persist a new valid guess (caller already checked dict + duplicate without lock)."""
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
        points=alphabetty_base_points(task) if status == 'correct' else 0,
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
    state = _read_actor_state(game=game, task=task, actor=actor)

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

    return _commit_guess(
        game=game,
        task=task,
        normalized=normalized,
        secret=secret,
        actor=actor,
        num=num,
        share_host=share_host,
    )


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
