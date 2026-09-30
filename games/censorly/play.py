"""Play state and guess handling for Цензурки."""

from __future__ import annotations

import json
from typing import Any, Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from games.censorly import CENSORLY_TAGS_KEY
from games.censorly.normalize import is_guessable_word, lemma_of, normalize_surface
from games.censorly.redact import (
    build_public_view,
    count_hits,
    lemmas_matching_guess,
    newly_revealed_ids,
)
from games.censorly.tokenize import title_content_lemmas
from games.models import Attempt, ChainTaskState, Game, Task


def default_state() -> dict[str, Any]:
    return {
        'guesses': [],
        'revealed_lemmas': [],
        'last_lemma': '',
        'won': False,
    }


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
    return state


def dump_state(state: dict[str, Any]) -> str:
    return json.dumps({
        'guesses': list(state.get('guesses') or []),
        'revealed_lemmas': list(state.get('revealed_lemmas') or []),
        'last_lemma': state.get('last_lemma') or '',
        'won': bool(state.get('won')),
    }, ensure_ascii=False)


def puzzle_from_task(task: Task) -> dict[str, Any] | None:
    tags = task.tags if isinstance(task.tags, dict) else {}
    payload = tags.get(CENSORLY_TAGS_KEY)
    if isinstance(payload, dict) and payload.get('title_tokens') is not None:
        return payload
    # Fallback: checker_data as JSON string
    raw = (task.checker_data or '').strip()
    if raw.startswith('{'):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if isinstance(data, dict) and data.get('title_tokens') is not None:
            return data
    return None


def _actor_filters(user=None, anon_key=None):
    if user is not None and getattr(user, 'is_authenticated', False):
        return {'user': user, 'team': None, 'anon_key': None}
    if anon_key:
        return {'user': None, 'team': None, 'anon_key': str(anon_key)}
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


def public_payload(
    state: dict[str, Any],
    payload: dict[str, Any],
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
    return {
        'guesses': guesses,
        'attempts': len(guesses),
        'won': won,
        'title_tokens': view['title_tokens'],
        'body_tokens': view['body_tokens'],
        'title_complete': view['title_complete'],
        'wiki_title': view.get('wiki_title'),
        'revealed_count': len(revealed),
    }


def get_play_state(
    *,
    game: Game,
    task: Task,
    user=None,
    anon_key=None,
) -> dict[str, Any]:
    payload = puzzle_from_task(task)
    if payload is None:
        return {'status': 'error', 'error': 'Пазл не настроен'}
    actor = _actor_filters(user=user, anon_key=anon_key)
    state = default_state() if actor is None else _read_actor_state(
        game=game, task=task, actor=actor,
    )
    out = public_payload(state, payload)
    out['status'] = 'ok'
    return out


@transaction.atomic
def apply_guess(
    *,
    game: Game,
    task: Task,
    word: str,
    user=None,
    anon_key=None,
) -> dict[str, Any]:
    payload = puzzle_from_task(task)
    if payload is None:
        return {'status': 'error', 'error': 'Пазл не настроен'}

    actor = _actor_filters(user=user, anon_key=anon_key)
    if actor is None:
        return {
            'status': 'error',
            'error': 'Нужен пользователь',
            **public_payload(default_state(), payload),
        }

    normalized = normalize_surface(word)
    if not normalized or not is_guessable_word(normalized):
        state = _read_actor_state(game=game, task=task, actor=actor)
        out = public_payload(state, payload)
        out['status'] = 'invalid'
        out['error'] = 'Введите одно слово (буквы/цифры)'
        return out

    guess_lemma = lemma_of(normalized)
    state = _read_actor_state(game=game, task=task, actor=actor)

    if state['won']:
        out = public_payload(state, payload)
        out['status'] = 'already_won'
        return out

    existing_words = {g.get('word') for g in state.get('guesses') or []}
    if normalized in existing_words:
        out = public_payload(state, payload)
        out['status'] = 'duplicate'
        out['error'] = 'Это слово уже вводили'
        return out

    matched = lemmas_matching_guess(payload, guess_lemma, normalized)

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
    row = ChainTaskState.objects.select_for_update().get(
        task=task,
        game=game,
        game_mode='general',
        **actor,
    )
    state = load_state(row.state)
    if state['won']:
        out = public_payload(state, payload)
        out['status'] = 'already_won'
        return out
    if normalized in {g.get('word') for g in state.get('guesses') or []}:
        out = public_payload(state, payload)
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
        primary_lemma = guess_lemma if guess_lemma in matched else next(iter(matched))

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

    attempt = Attempt(
        task=task,
        game=game,
        text=normalized,
        status='Ok' if won else ('Partial' if hits else 'Wrong'),
        points=1 if won else 0,
        state=dump_state(state),
        **actor,
    )
    attempt.time = timezone.now()
    attempt.save()

    row.state = dump_state(state)
    row.last_attempt = attempt
    row.save(update_fields=['state', 'last_attempt', 'updated_at'])

    out = public_payload(state, payload)
    if won:
        out['status'] = 'won'
    elif hits:
        out['status'] = 'hit'
    elif matched:
        out['status'] = 'already_open'
    else:
        out['status'] = 'miss'
    out['hits'] = hits
    out['newly_revealed'] = newly
    out['guess_word'] = normalized
    return out


def reset_progress(*, game: Game, task: Task, user=None, anon_key=None) -> int:
    """Delete ChainTaskState (+ optional attempts) for one actor. Returns deleted states."""
    actor = _actor_filters(user=user, anon_key=anon_key)
    if actor is None:
        return 0
    deleted, _ = ChainTaskState.objects.filter(
        task=task,
        game=game,
        game_mode='general',
        **actor,
    ).delete()
    Attempt.manager.filter(task=task, game=game, **actor).delete()
    return deleted
