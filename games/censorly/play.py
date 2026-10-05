"""Play state and guess/hint handling for Цензурки."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from games.censorly import CENSORLY_SHOW_MASK_ENDINGS, CENSORLY_TAGS_KEY
from games.censorly.normalize import is_guessable_word, lemma_of, normalize_surface
from games.censorly.redact import (
    build_public_view,
    count_hits,
    lemmas_matching_guess,
    newly_revealed_ids,
    token_by_id,
)
from games.censorly.tokenize import title_content_lemmas, upgrade_puzzle_payload
from games.models import Attempt, ChainTaskState, Game, Task
from games.results.share import format_elapsed, format_share_link, share_path

CENSORLY_BASE_POINTS = 20
CENSORLY_HINT_PENALTY = 1


def default_state() -> dict[str, Any]:
    return {
        'guesses': [],
        'revealed_lemmas': [],
        'last_lemma': '',
        'won': False,
        'hints_taken': 0,
    }


def hint_count(state: dict[str, Any]) -> int:
    try:
        return max(0, int(state.get('hints_taken') or 0))
    except (TypeError, ValueError):
        return 0


def points_for_state(state: dict[str, Any], *, task: Task | None = None) -> Decimal:
    base = Decimal(CENSORLY_BASE_POINTS)
    if task is not None:
        try:
            p = task.get_points()
            if p is not None:
                base = Decimal(str(p))
        except Exception:
            pass
    return max(Decimal('0'), base - Decimal(hint_count(state) * CENSORLY_HINT_PENALTY))


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
    cleaned = []
    if isinstance(guesses, list):
        for item in guesses:
            if isinstance(item, dict):
                word = normalize_surface(item.get('word') or '')
                lemma = normalize_surface(item.get('lemma') or '') or lemma_of(word)
                try:
                    hits = int(item.get('hits') or 0)
                except (TypeError, ValueError):
                    hits = 0
                if word:
                    cleaned.append({'word': word, 'lemma': lemma, 'hits': max(0, hits)})
            elif isinstance(item, str) and normalize_surface(item):
                w = normalize_surface(item)
                cleaned.append({'word': w, 'lemma': lemma_of(w), 'hits': 0})
    state['guesses'] = cleaned
    lemmas = data.get('revealed_lemmas') or []
    if isinstance(lemmas, list):
        state['revealed_lemmas'] = [
            normalize_surface(x) for x in lemmas if normalize_surface(str(x))
        ]
    state['last_lemma'] = normalize_surface(data.get('last_lemma') or '')
    state['won'] = bool(data.get('won'))
    state['hints_taken'] = hint_count(data)
    return state


def dump_state(state: dict[str, Any]) -> str:
    return json.dumps({
        'guesses': list(state.get('guesses') or []),
        'revealed_lemmas': list(state.get('revealed_lemmas') or []),
        'last_lemma': state.get('last_lemma') or '',
        'won': bool(state.get('won')),
        'hints_taken': hint_count(state),
    }, ensure_ascii=False)


def puzzle_from_task(task: Task) -> dict[str, Any] | None:
    tags = task.tags if isinstance(task.tags, dict) else {}
    payload = tags.get(CENSORLY_TAGS_KEY)
    if isinstance(payload, dict) and payload.get('title_tokens') is not None:
        return upgrade_puzzle_payload(payload)
    raw = (task.checker_data or '').strip()
    if raw.startswith('{'):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if isinstance(data, dict) and data.get('title_tokens') is not None:
            return upgrade_puzzle_payload(data)
    return None


def _actor_filters(user=None, anon_key=None, replay_slot=None):
    if user is not None and getattr(user, 'is_authenticated', False):
        return {'user': user, 'team': None, 'anon_key': None, 'replay_slot': replay_slot}
    if anon_key:
        return {'user': None, 'team': None, 'anon_key': str(anon_key), 'replay_slot': replay_slot}
    return None


def _read_actor_state(*, game: Game, task: Task, actor: dict) -> dict[str, Any]:
    row = ChainTaskState.objects.filter(
        task=task,
        game=game,
        game_mode='general',
        **actor,
    ).first()
    if row is None:
        return default_state()
    return load_state(row.state)


def _title_won(payload: dict[str, Any], revealed: set[str]) -> bool:
    needed = title_content_lemmas(payload)
    if not needed:
        return False
    return needed <= revealed


def ru_attempt_word(n: int) -> str:
    n = abs(int(n))
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return 'попытка'
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return 'попытки'
    return 'попыток'


def ru_hint_word(n: int) -> str:
    n = abs(int(n))
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return 'подсказка'
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return 'подсказки'
    return 'подсказок'


def build_share_lines(
    *,
    number: int | str,
    attempts: int,
    elapsed_seconds: int,
    hints: int = 0,
    host: str = 'interoves.com',
    play_path: str | None = None,
) -> list[str]:
    display_number = str(number or '').strip()
    path = share_path(play_path) if play_path else 'censorly/{}'.format(display_number)
    title = f'🖍️ Цензурка #{display_number}' if display_number else '🖍️ Цензурка'
    lines = [title, f'🤔 {attempts} {ru_attempt_word(attempts)}']
    if hints:
        lines.append(f'💡 {hints} {ru_hint_word(hints)}')
    lines.extend([
        f'⏱️ {format_elapsed(elapsed_seconds)}',
        format_share_link(host, path),
    ])
    return lines


def elapsed_seconds_for_actor(*, game: Game, task: Task, actor: dict) -> int:
    from games.daily_timing import canonical_elapsed_seconds

    attempts = list(
        Attempt.manager.filter(task=task, game=game, **actor).exclude(time__isnull=True)
    )
    return canonical_elapsed_seconds(
        game=game,
        task_group=getattr(task, 'task_group', None),
        user=actor.get('user'),
        anon_key=actor.get('anon_key'),
        replay_slot=actor.get('replay_slot'),
        attempts=attempts,
    )


def attach_solve_meta(
    payload: dict[str, Any],
    *,
    game: Game,
    task: Task,
    number: int | str,
    actor: dict | None,
    host: str = 'interoves.com',
    play_path: str | None = None,
) -> dict[str, Any]:
    attempts = int(payload.get('attempts') or 0)
    hints = int(payload.get('hints') or 0)
    payload['attempts_label'] = f'{attempts} {ru_attempt_word(attempts)}'
    pts = max(0, CENSORLY_BASE_POINTS - hints * CENSORLY_HINT_PENALTY)
    try:
        base = task.get_points()
        if base is not None:
            pts = max(0, int(base) - hints * CENSORLY_HINT_PENALTY)
    except Exception:
        pass
    payload['points'] = pts
    if not payload.get('won') or not actor:
        return payload
    elapsed = elapsed_seconds_for_actor(game=game, task=task, actor=actor)
    payload['elapsed_seconds'] = elapsed
    payload['elapsed_label'] = format_elapsed(elapsed)
    if actor.get('replay_slot') is not None:
        # Replay results are private and must never produce a public share card.
        return payload
    lines = build_share_lines(
        number=number,
        attempts=attempts,
        elapsed_seconds=elapsed,
        hints=hints,
        host=host,
        play_path=play_path,
    )
    payload['share_lines'] = lines
    payload['share_text'] = '\n'.join(lines)
    from games.censorly.redact import unsolved_share_runs
    from games.daily_share_card import build_censorly_share_payload, publish_date_for
    from games.models import GameTaskGroup

    placement = None
    if getattr(task, 'task_group_id', None):
        placement = (
            GameTaskGroup.objects
            .filter(game_id=game.pk, task_group_id=task.task_group_id)
            .only('number')
            .first()
        )
    puzzle = puzzle_from_task(task)
    if puzzle:
        title_runs, lead_runs = unsolved_share_runs(puzzle)
    else:
        title_runs, lead_runs = [], []
    payload['share_card'] = build_censorly_share_payload(
        number=number,
        date_value=publish_date_for(game, getattr(placement, 'number', number)),
        elapsed_seconds=elapsed,
        attempts=attempts,
        hints=hints,
        article_title_runs=title_runs,
        article_lead_runs=lead_runs,
        locale='ru',
        brand_host=host,
        play_path=play_path,
    )
    return payload


def public_payload(
    state: dict[str, Any],
    payload: dict[str, Any],
    *,
    task: Task | None = None,
) -> dict[str, Any]:
    revealed = set(state.get('revealed_lemmas') or [])
    won = bool(state.get('won')) or _title_won(payload, revealed)
    view = build_public_view(
        payload,
        revealed_lemmas=revealed,
        last_lemma=state.get('last_lemma') or None,
        won=won,
    )
    guesses = list(state.get('guesses') or [])
    hints = hint_count(state)
    pts = points_for_state(state, task=task)
    return {
        'guesses': guesses,
        'attempts': len(guesses),
        'attempts_label': f'{len(guesses)} {ru_attempt_word(len(guesses))}',
        'won': won,
        'title_tokens': view['title_tokens'],
        'body_tokens': view['body_tokens'],
        'title_complete': view['title_complete'],
        'wiki_title': view.get('wiki_title'),
        'revealed_count': len(revealed),
        'hints': hints,
        'hints_taken': hints,
        'points': float(pts),
        'max_points': float(task.get_points() if task is not None else CENSORLY_BASE_POINTS),
        'hint_penalty': CENSORLY_HINT_PENALTY,
        'show_mask_endings': bool(view.get('show_mask_endings', CENSORLY_SHOW_MASK_ENDINGS)),
        'truncated': bool(payload.get('truncated')),
        # Only after win — curid/title links must not spoil the article.
        'wiki_pageid': payload.get('wiki_pageid') if won else None,
    }


def get_play_state(
    *,
    game: Game,
    task: Task,
    user=None,
    anon_key=None,
    number: int | str | None = None,
    share_host: str = 'interoves.com',
    play_path: str | None = None,
    replay_slot=None,
) -> dict[str, Any]:
    payload = puzzle_from_task(task)
    if payload is None:
        return {'status': 'error', 'error': 'Пазл не настроен'}
    actor = _actor_filters(user=user, anon_key=anon_key, replay_slot=replay_slot)
    state = default_state() if actor is None else _read_actor_state(
        game=game, task=task, actor=actor,
    )
    out = public_payload(state, payload, task=task)
    out['status'] = 'ok'
    if out.get('won') and actor is not None:
        attach_solve_meta(
            out,
            game=game,
            task=task,
            number=number if number is not None else '',
            actor=actor,
            host=share_host,
            play_path=play_path,
        )
    return out


def _lock_state(*, game, task, actor):
    try:
        ChainTaskState.objects.get_or_create(
            task=task,
            game=game,
            game_mode='general',
            defaults={'state': dump_state(default_state())},
            **actor,
        )
    except IntegrityError:
        pass
    return ChainTaskState.objects.select_for_update().get(
        task=task,
        game=game,
        game_mode='general',
        **actor,
    )


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
    play_path: str | None = None,
    replay_slot=None,
) -> dict[str, Any]:
    payload = puzzle_from_task(task)
    if payload is None:
        return {'status': 'error', 'error': 'Пазл не настроен'}

    actor = _actor_filters(user=user, anon_key=anon_key, replay_slot=replay_slot)
    if actor is None:
        return {
            'status': 'error',
            'error': 'Нужен пользователь',
            **public_payload(default_state(), payload, task=task),
        }

    normalized = normalize_surface(word)
    if not normalized or not is_guessable_word(normalized):
        state = _read_actor_state(game=game, task=task, actor=actor)
        out = public_payload(state, payload, task=task)
        out['status'] = 'invalid'
        out['error'] = 'Введите одно слово (буквы/цифры)'
        return out

    guess_lemma = lemma_of(normalized)
    state = _read_actor_state(game=game, task=task, actor=actor)
    num = number if number is not None else ''

    if state['won']:
        out = public_payload(state, payload, task=task)
        out['status'] = 'already_won'
        return attach_solve_meta(
            out, game=game, task=task, number=num, actor=actor,
            host=share_host, play_path=play_path,
        )

    if normalized in {g.get('word') for g in state.get('guesses') or []}:
        out = public_payload(state, payload, task=task)
        out['status'] = 'duplicate'
        out['error'] = 'Это слово уже вводили'
        return out

    matched = lemmas_matching_guess(payload, guess_lemma, normalized)
    row = _lock_state(game=game, task=task, actor=actor)
    state = load_state(row.state)
    if state['won']:
        out = public_payload(state, payload, task=task)
        out['status'] = 'already_won'
        return attach_solve_meta(
            out, game=game, task=task, number=num, actor=actor,
            host=share_host, play_path=play_path,
        )
    if normalized in {g.get('word') for g in state.get('guesses') or []}:
        out = public_payload(state, payload, task=task)
        out['status'] = 'duplicate'
        out['error'] = 'Это слово уже вводили'
        return out

    already_revealed = set(state.get('revealed_lemmas') or [])
    new_matched = matched - already_revealed
    hits = 0
    newly: list[int] = []
    primary_lemma = ''
    if new_matched:
        primary_lemma = guess_lemma if guess_lemma in new_matched else next(iter(new_matched))
        for lem in new_matched:
            hits += count_hits(payload, lem)
            newly.extend(newly_revealed_ids(payload, lem))
    elif matched:
        # Already-open lemma (other surface form): do not consume an attempt.
        primary_lemma = guess_lemma if guess_lemma in matched else next(iter(matched))
        out = public_payload(state, payload, task=task)
        out['status'] = 'already_open'
        out['hits'] = 0
        out['newly_revealed'] = []
        out['guess_word'] = normalized
        out['error'] = 'Уже открыто'
        return out

    revealed = already_revealed | new_matched
    state['revealed_lemmas'] = sorted(revealed)
    state['guesses'] = list(state.get('guesses') or []) + [{
        'word': normalized,
        'lemma': primary_lemma or guess_lemma,
        'hits': hits,
    }]
    state['last_lemma'] = primary_lemma if hits else ''
    won = _title_won(payload, revealed)
    state['won'] = won
    pts = points_for_state(state, task=task)

    attempt = Attempt(
        task=task,
        game=game,
        text=normalized,
        status='Ok' if won else ('Partial' if hits else 'Wrong'),
        points=pts if won else 0,
        state=dump_state(state),
        **actor,
    )
    attempt.time = timezone.now()
    if won:
        from games.daily_timing import active_time_ms_for_attempt
        attempt.active_time_ms = active_time_ms_for_attempt(
            game=game, task_group=task.task_group,
            user=actor.get('user'), anon_key=actor.get('anon_key'),
            replay_slot=actor.get('replay_slot'),
            now=attempt.time,
        )
    attempt.save()

    row.state = dump_state(state)
    row.last_attempt = attempt
    row.save(update_fields=['state', 'last_attempt', 'updated_at'])

    out = public_payload(state, payload, task=task)
    out['hits'] = hits
    out['newly_revealed'] = newly
    out['guess_word'] = normalized
    if won:
        out['status'] = 'won'
        return attach_solve_meta(
            out, game=game, task=task, number=num, actor=actor,
            host=share_host, play_path=play_path,
        )
    if hits:
        out['status'] = 'hit'
    else:
        out['status'] = 'miss'
    return out


@transaction.atomic
def apply_hint(
    *,
    game: Game,
    task: Task,
    token_id: int,
    user=None,
    anon_key=None,
    number: int | str | None = None,
    share_host: str = 'interoves.com',
    play_path: str | None = None,
    replay_slot=None,
) -> dict[str, Any]:
    """Reveal one non-title content lemma for −1 point."""
    payload = puzzle_from_task(task)
    if payload is None:
        return {'status': 'error', 'error': 'Пазл не настроен'}
    actor = _actor_filters(user=user, anon_key=anon_key, replay_slot=replay_slot)
    if actor is None:
        return {'status': 'error', 'error': 'Нужен пользователь'}

    tok = token_by_id(payload, int(token_id))
    if tok is None or tok.get('kind') != 'content':
        return {'status': 'error', 'error': 'Выберите скрытое слово'}
    lemma = tok.get('lemma') or ''
    if not lemma:
        return {'status': 'error', 'error': 'Нечего открывать'}
    if lemma in title_content_lemmas(payload):
        return {
            'status': 'error',
            'error': 'Нельзя открывать слова из названия',
        }

    num = number if number is not None else ''
    row = _lock_state(game=game, task=task, actor=actor)
    state = load_state(row.state)
    if state['won']:
        out = public_payload(state, payload, task=task)
        out['status'] = 'already_won'
        return attach_solve_meta(
            out, game=game, task=task, number=num, actor=actor,
            host=share_host, play_path=play_path,
        )
    if lemma in set(state.get('revealed_lemmas') or []):
        out = public_payload(state, payload, task=task)
        out['status'] = 'already_open'
        out['error'] = 'Слово уже открыто'
        return out

    revealed = set(state.get('revealed_lemmas') or [])
    revealed.add(lemma)
    state['revealed_lemmas'] = sorted(revealed)
    state['last_lemma'] = lemma
    state['hints_taken'] = hint_count(state) + 1
    won = _title_won(payload, revealed)
    state['won'] = won
    newly = newly_revealed_ids(payload, lemma)
    pts = points_for_state(state, task=task)

    attempt = Attempt(
        task=task,
        game=game,
        text=f'#hint:{lemma}',
        status='Ok' if won else 'Partial',
        points=pts if won else 0,
        state=dump_state(state),
        **actor,
    )
    attempt.time = timezone.now()
    if won:
        from games.daily_timing import active_time_ms_for_attempt
        attempt.active_time_ms = active_time_ms_for_attempt(
            game=game, task_group=task.task_group,
            user=actor.get('user'), anon_key=actor.get('anon_key'),
            replay_slot=actor.get('replay_slot'),
            now=attempt.time,
        )
    attempt.save()
    row.state = dump_state(state)
    row.last_attempt = attempt
    row.save(update_fields=['state', 'last_attempt', 'updated_at'])

    out = public_payload(state, payload, task=task)
    out['newly_revealed'] = newly
    out['hint_lemma'] = lemma
    if won:
        out['status'] = 'won'
        return attach_solve_meta(
            out, game=game, task=task, number=num, actor=actor,
            host=share_host, play_path=play_path,
        )
    out['status'] = 'hint'
    return out


def get_task_for_number(game: Game, number: int | str):
    from games.models import GameTaskGroup

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


@transaction.atomic
def reset_progress(*, game: Game, task: Task, user=None, anon_key=None) -> int:
    actor = _actor_filters(user=user, anon_key=anon_key)
    if actor is None:
        return 0
    chain_qs = ChainTaskState.objects.filter(
        task=task,
        game=game,
        game_mode='general',
        **actor,
    )
    attempt_qs = Attempt.manager.filter(task=task, game=game, **actor)
    from games.daily_result_projection import mark_projection_dirty
    mark_projection_dirty(game, task.task_group, full=True)
    n_attempts = attempt_qs.count()
    chain_qs.delete()
    attempt_qs.delete()
    from games.targeted_completion_reconciliation import reconcile_task_group_actors
    reconcile_task_group_actors(
        game_id=game.pk,
        task_group_id=task.task_group_id,
        actor_keys={(actor['team'].pk if actor['team'] else None,
                     actor['user'].pk if actor['user'] else None,
                     actor['anon_key'])},
    )
    return n_attempts
