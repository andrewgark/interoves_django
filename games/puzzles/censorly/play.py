"""Play state and guess/hint handling for Цензурки."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from itertools import chain
from typing import Any
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone

from games.middleware.request_timing import timing_phase
from games.puzzles.censorly.ids import (
    CENSORLY_SHOW_MASK_ENDINGS,
    CENSORLY_SPLITTER_VERSION,
    CENSORLY_TAGS_KEY,
)
from games.puzzles.censorly.text.normalize import is_guessable_word, lemma_of, normalize_surface
from games.puzzles.censorly.article.redact import (
    build_public_view,
    lemmas_matching_guess,
    newly_revealed_ids,
    title_opening_lemmas,
    token_by_id,
    token_ids_for_guess,
)
from games.puzzles.censorly.text.stopwords import is_stop_word
from games.puzzles.censorly.text.tokenize import all_tokens, resolve_puzzle_payload, title_content_lemmas
from games.models import Attempt, ChainTaskState, Game, Task
from games.puzzles.core.base import Puzzle
from games.results.share import format_elapsed, format_share_link, share_path

CENSORLY_BASE_POINTS = 20
CENSORLY_HINT_PENALTY = 1
_WIKI_FETCHED_TZ = ZoneInfo('Europe/Moscow')
# Phone keyboards autocorrect a word into «кот.» or wrap it in quotes.
_GUESS_EDGE_PUNCTUATION = ' \t.,!?;:…"\'«»„“”()[]'
_RU_MONTHS_GENITIVE = (
    '',
    'января',
    'февраля',
    'марта',
    'апреля',
    'мая',
    'июня',
    'июля',
    'августа',
    'сентября',
    'октября',
    'ноября',
    'декабря',
)


def wiki_fetched_label(value) -> str:
    """Player-facing date of the frozen Wikipedia snapshot. Empty if unknown."""
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or '').strip()
        if not text:
            return ''
        try:
            moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            return ''
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, timezone.get_current_timezone())
    local = moment.astimezone(_WIKI_FETCHED_TZ)
    return (
        'Версия статьи в Википедии на момент '
        f'{local.day} {_RU_MONTHS_GENITIVE[local.month]} {local.year} года'
    )


def default_state() -> dict[str, Any]:
    return {
        'guesses': [],
        'revealed_lemmas': [],
        'hinted_lemmas': [],
        'last_lemma': '',
        'won': False,
        'hints_taken': 0,
    }


def hint_count(state: dict[str, Any]) -> int:
    try:
        return max(0, int(state.get('hints_taken') or 0))
    except (TypeError, ValueError):
        return 0


def _clean_lemma_list(value: Any) -> list[str]:
    """Normalize persisted lemma arrays while ignoring malformed values."""
    if not isinstance(value, list):
        return []
    cleaned = [normalize_surface(item) for item in value if isinstance(item, str)]
    return [lemma for lemma in cleaned if lemma]


def _is_initially_open_word(surface: str) -> bool:
    """Whether *surface* is visible before a player makes a guess."""
    if is_stop_word(surface):
        return True
    from games.puzzles.censorly.text.flags import lexical_resolver_enabled
    if not lexical_resolver_enabled():
        return False
    from games.puzzles.censorly.text.flags import open_pronouns_enabled
    from games.puzzles.censorly.lexical.semantics import initially_open
    return initially_open(surface, include_pronouns=open_pronouns_enabled())


def _has_closed_exact_target(payload: dict[str, Any], guess: str) -> bool:
    """An open spelling may also name a closed, explicitly parsed reading."""
    normalized = normalize_surface(guess)
    return any(
        tok.get('kind') == 'content'
        and tok.get('initially_open') is False
        and normalize_surface(tok.get('surface') or '') == normalized
        for tok in all_tokens(payload)
    )


def hub_progress_for_actor(*, game: Game, numbers_and_tasks: list[tuple[int, Task]], user=None, anon_key=None) -> dict[int, dict[str, Any]]:
    """Return compact solve progress for the daily Censorly archive."""
    actor = _actor_filters(user=user, anon_key=anon_key)
    if actor is None or not numbers_and_tasks:
        return {}
    task_ids = [task.pk for _, task in numbers_and_tasks]
    states = {
        row.task_id: load_state(row.state)
        for row in ChainTaskState.objects.filter(
            game=game, game_mode='general', replay_slot__isnull=True,
            task_id__in=task_ids, **actor,
        )
    }
    out = {}
    for number, task in numbers_and_tasks:
        state = states.get(task.pk) or default_state()
        attempts = len(state.get('guesses') or [])
        hints = hint_count(state)
        payload = puzzle_from_task(task)
        won = bool(payload and _state_won(state, payload))
        if not won and not attempts and not hints:
            continue
        parts = []
        if attempts:
            parts.append(f'🔄 {attempts} {ru_attempt_word(attempts)}')
        if hints:
            parts.append(f'💡 {hints} {ru_hint_word(hints)}')
        out[number] = {
            'is_solved': won,
            'progress_meta': '  '.join(parts),
            'row_class': 'new-task--solved' if won else 'new-task--partial',
        }
    return out


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
                raw_word = item.get('word')
                if not isinstance(raw_word, str):
                    continue
                word = normalize_surface(raw_word)
                raw_lemma = item.get('lemma')
                lemma = (
                    normalize_surface(raw_lemma)
                    if isinstance(raw_lemma, str) else ''
                ) or lemma_of(word)
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
    state['revealed_lemmas'] = _clean_lemma_list(data.get('revealed_lemmas'))
    last_lemma = data.get('last_lemma')
    state['last_lemma'] = normalize_surface(last_lemma) if isinstance(last_lemma, str) else ''
    state['hinted_lemmas'] = sorted(set(_clean_lemma_list(data.get('hinted_lemmas'))))
    state['won'] = bool(data.get('won'))
    state['hints_taken'] = hint_count(data)
    return state


def dump_state(state: dict[str, Any]) -> str:
    return json.dumps({
        'guesses': list(state.get('guesses') or []),
        'revealed_lemmas': list(state.get('revealed_lemmas') or []),
        'last_lemma': state.get('last_lemma') or '',
        'hinted_lemmas': list(state.get('hinted_lemmas') or []),
        'won': bool(state.get('won')),
        'hints_taken': hint_count(state),
    }, ensure_ascii=False)


def _persist_resolved_puzzle(task: Task, resolved: dict[str, Any], *, where: str) -> None:
    if not getattr(task, 'pk', None):
        return
    if where == 'tags':
        old_tags = task.tags if isinstance(task.tags, dict) else {}
        tags = dict(old_tags)
        tags[CENSORLY_TAGS_KEY] = resolved
        new_revision = uuid.uuid4()
        updated = Task.objects.filter(pk=task.pk, tags=old_tags).update(
            tags=tags,
            attempt_revision=new_revision,
        )
        if updated:
            task.tags = tags
            task.attempt_revision = new_revision
        return
    old_checker_data = task.checker_data
    new_checker_data = json.dumps(resolved, ensure_ascii=False)
    new_revision = uuid.uuid4()
    updated = Task.objects.filter(
        pk=task.pk, checker_data=old_checker_data,
    ).update(
        checker_data=new_checker_data,
        attempt_revision=new_revision,
    )
    if updated:
        task.checker_data = new_checker_data
        task.attempt_revision = new_revision


def _stored_puzzle_payload(task: Task) -> tuple[dict[str, Any] | None, str]:
    """Return the stored Цензурка payload and its storage column."""
    tags = task.tags if isinstance(getattr(task, 'tags', None), dict) else {}
    payload = tags.get(CENSORLY_TAGS_KEY) if isinstance(tags, dict) else None
    if isinstance(payload, dict) and (
        payload.get('title_tokens') is not None or isinstance(payload.get('body_text'), str)
    ):
        return payload, 'tags'

    raw = (getattr(task, 'checker_data', None) or '').strip()
    if raw.startswith('{'):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict) and (
            data.get('title_tokens') is not None or isinstance(data.get('body_text'), str)
        ):
            return data, 'checker'
    return None, ''


@lru_cache(maxsize=256)
def _cached_current_puzzle_payload(
    task_id: int,
    attempt_revision: str,
    splitter_version: int,
) -> dict[str, Any] | None:
    """Cache the immutable, already-tokenized puzzle between requests.

    The cache deliberately contains no actor state. ``attempt_revision`` is
    bumped by Task.save(), while the splitter version is part of the key so a
    tokenizer change cannot reuse an old representation.
    """
    task = Task.objects.only('tags', 'checker_data').get(pk=task_id)
    payload, _where = _stored_puzzle_payload(task)
    if payload is None:
        return None
    resolved = resolve_puzzle_payload(payload)
    if resolved.get('splitter_version') != CENSORLY_SPLITTER_VERSION:
        return None
    return resolved


def puzzle_from_task(task: Task, *, persist: bool = True) -> dict[str, Any] | None:
    """Load a puzzle and rebuild its split when the splitter version moved.

    Article text is not downloaded again. A stale cache is written back once
    so the next read uses the current tokens.
    """
    payload, where = _stored_puzzle_payload(task)
    if payload is None:
        return None
    if (
        getattr(task, 'pk', None)
        and getattr(task, 'attempt_revision', None)
        and payload.get('splitter_version') == CENSORLY_SPLITTER_VERSION
        and isinstance(payload.get('body_text'), str)
        and payload.get('title_tokens') is not None
    ):
        return _cached_current_puzzle_payload(
            task.pk,
            str(task.attempt_revision),
            CENSORLY_SPLITTER_VERSION,
        )
    resolved = resolve_puzzle_payload(payload)
    stale = (
        not isinstance(payload.get('body_text'), str)
        or payload.get('splitter_version') != CENSORLY_SPLITTER_VERSION
    )
    if persist and stale and resolved is not payload and where:
        _persist_resolved_puzzle(task, resolved, where=where)
    return resolved


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


def _state_won(state: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Treat legacy states with every title lemma revealed as solved."""
    return bool(state.get('won')) or _title_won(
        payload, set(state.get('revealed_lemmas') or [])
    )


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
    title = f'📰 Цензурка #{display_number}' if display_number else '📰 Цензурка'
    lines = [title, f'🔄 {attempts} {ru_attempt_word(attempts)}']
    if hints:
        lines.append(f'💡 {hints} {ru_hint_word(hints)}')
    lines.extend([
        f'⏱️ {format_elapsed(elapsed_seconds)}',
        format_share_link(host, path),
    ])
    return lines


def elapsed_seconds_for_actor(*, game: Game, task: Task, actor: dict) -> int:
    from games.daily.timing import canonical_elapsed_seconds

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
    from games.puzzles.censorly.article.redact import unsolved_share_runs
    from games.daily.page.share.card import build_censorly_share_payload, publish_date_for
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
    cache_key = _lexical_cache_key(task)
    history_tokens = None
    ids_by_lemma: dict[str, list[int]] | None = None
    revealed = set(state.get('revealed_lemmas') or [])
    won = _state_won(state, payload)
    view = build_public_view(
        payload,
        revealed_lemmas=revealed,
        last_lemma=state.get('last_lemma') or None,
        hinted_lemmas=state.get('hinted_lemmas') or (),
        won=won,
    )
    public_body_by_id = {token['id']: token for token in view['body_tokens']}
    body_word_tokens = []
    for token in payload.get('body_tokens') or []:
        if token.get('kind') == 'content':
            body_word_tokens.append(token)
        elif token.get('kind') == 'stop':
            try:
                is_word = int(token.get('length') or 0) > 0
            except (TypeError, ValueError):
                is_word = False
            if is_word:
                body_word_tokens.append(token)
    guesses = []
    for guess in state.get('guesses') or []:
        if not isinstance(guess, dict):
            continue
        if history_tokens is None:
            history_tokens = all_tokens(payload)
            ids_by_lemma = {}
            for tok in history_tokens:
                if tok.get('kind') == 'content':
                    ids_by_lemma.setdefault(tok.get('lemma') or '', []).append(int(tok['id']))
        item = dict(guess)
        item['token_ids'] = token_ids_for_guess(
            payload, item.get('lemma') or '', item.get('word') or '',
            cache_key=cache_key, tokens=history_tokens,
            ids_by_lemma=ids_by_lemma,
        )
        guesses.append(item)
    hints = hint_count(state)
    pts = points_for_state(state, task=task)
    return {
        'guesses': guesses,
        'attempts': len(guesses),
        'attempts_label': f'{len(guesses)} {ru_attempt_word(len(guesses))}',
        'won': won,
        'title_tokens': view['title_tokens'],
        'body_tokens': view['body_tokens'],
        'opened_words_count': sum(
            bool(public_body_by_id.get(token['id'], {}).get('revealed'))
            for token in body_word_tokens
        ),
        'total_words_count': len(body_word_tokens),
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
        'wiki_fetched_label': wiki_fetched_label(payload.get('fetched_at')),
        # Only after win — curid/title links must not spoil the article.
        'wiki_pageid': payload.get('wiki_pageid') if won else None,
    }


def _lexical_cache_key(task: Task | None):
    if getattr(task, 'pk', None) and getattr(task, 'attempt_revision', None):
        return (task.pk, str(task.attempt_revision), CENSORLY_SPLITTER_VERSION)
    return None


def _history_index_for_guess(
    state: dict[str, Any],
    payload: dict[str, Any],
    *,
    word: str = '',
    target_lemmas: set[str] | None = None,
    task: Task | None = None,
    public_guesses: list[dict[str, Any]] | None = None,
) -> int | None:
    """Find the history row which originally opened a repeated word."""
    guesses = state.get('guesses') or []
    for index, guess in enumerate(guesses):
        if isinstance(guess, dict) and word and guess.get('word') == word:
            return index
    if not target_lemmas:
        return None
    cache_key = _lexical_cache_key(task)
    target_ids = {
        int(tok['id'])
        for tok in all_tokens(payload)
        if tok.get('kind') == 'content' and tok.get('lemma') in target_lemmas
    }
    if not target_ids:
        return None
    if public_guesses is not None:
        for index, guess in enumerate(public_guesses):
            if not isinstance(guess, dict):
                continue
            if target_ids.intersection(guess.get('token_ids') or ()):
                return index
        return None
    for index, guess in enumerate(guesses):
        if not isinstance(guess, dict):
            continue
        opened_ids = token_ids_for_guess(
            payload, guess.get('lemma') or '', guess.get('word') or '',
            cache_key=cache_key,
        )
        if target_ids.intersection(opened_ids):
            return index
    return None


def _matching_token_ids(
    payload: dict[str, Any], lemmas: set[str],
) -> tuple[str, dict[str, list[int]]]:
    """Return first matching article lemma and token IDs in one token pass."""
    token_ids = {lemma: [] for lemma in lemmas}
    first_lemma = ''
    for tok in chain(
        payload.get('title_tokens') or (),
        payload.get('body_tokens') or (),
    ):
        if tok.get('kind') != 'content':
            continue
        lemma = tok.get('lemma') or ''
        if lemma not in token_ids:
            continue
        if not first_lemma:
            first_lemma = lemma
        token_ids[lemma].append(int(tok['id']))
    return first_lemma, token_ids


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


def _record_lexical_decision(*, user, game, task, guess, payload, report=None) -> None:
    """Store which targets a guess opened and which near-misses it rejected."""
    from games.puzzles.censorly.text.flags import lexical_resolver_enabled
    if not lexical_resolver_enabled():
        return
    try:
        from games.puzzles.censorly.lexical.match import decision
        from games.puzzles.censorly.text.tokenize import all_tokens
        from games.models import StatisticsEvent
        if report is None:
            report = decision(all_tokens(payload), guess)
        actor = user if getattr(user, 'is_authenticated', False) else None
        with transaction.atomic():
            StatisticsEvent.record(
                StatisticsEvent.KIND_CENSORLY_LEXICAL_GUESS,
                user=actor,
                guess=guess,
                task_id=getattr(task, 'pk', None),
                game_id=getattr(game, 'pk', None),
                **report,
            )
    except Exception:
        logging.getLogger('games.puzzles.censorly.play').exception('censorly lexical decision log failed')


@dataclass(frozen=True)
class _Move:
    """One actor's guess or hint in one puzzle, and how its result is shared.

    Guesses and hints follow the same steps: validate the input, answer moves
    that change nothing from an unlocked read, ``timing.check()`` before any
    expensive work, then lock the timer lease and the state, re-check, and
    save.
    """

    game: Game
    task: Task
    payload: dict
    actor: dict
    number: int | str
    share_host: str
    play_path: str | None
    submitted_by_profile_id: int | None
    attempt_created_callback: Any
    timing: Any
    timing_request: Any

    def view(self, state: dict, status: str, **fields) -> dict[str, Any]:
        out = public_payload(state, self.payload, task=self.task)
        out['status'] = status
        out.update(fields)
        return out

    def solved(self, out: dict[str, Any]) -> dict[str, Any]:
        return attach_solve_meta(
            out, game=self.game, task=self.task, number=self.number, actor=self.actor,
            host=self.share_host, play_path=self.play_path,
        )

    def already_won(self, state: dict) -> dict[str, Any] | None:
        if not _state_won(state, self.payload):
            return None
        return self.solved(self.view(state, 'already_won'))

    def read_state(self) -> dict:
        with timing_phase(self.timing_request, 'actor_state_read'):
            return _read_actor_state(game=self.game, task=self.task, actor=self.actor)

    def check_timing(self) -> None:
        if self.timing is not None:
            self.timing.check()

    def lock_state(self):
        """Lock the timer lease, then the actor state (always in this order)."""
        if self.timing is not None:
            self.timing.lock()
        with timing_phase(self.timing_request, 'state_lock'):
            row = _lock_state(game=self.game, task=self.task, actor=self.actor)
        with timing_phase(self.timing_request, 'state_decode'):
            return row, load_state(row.state)

    def save(self, row, state: dict, *, text: str, status: str) -> None:
        """Store a move that changed the state, with its Attempt."""
        won = bool(state.get('won'))
        attempt = Attempt(
            task=self.task,
            game=self.game,
            submitted_by_profile_id=self.submitted_by_profile_id,
            text=text,
            status='Ok' if won else status,
            points=points_for_state(state, task=self.task) if won else 0,
            state=dump_state(state),
            **self.actor,
        )
        attempt.time = timezone.now()
        if won:
            from games.daily.timing import active_time_ms_for_attempt
            attempt.active_time_ms = active_time_ms_for_attempt(
                game=self.game, task_group=self.task.task_group,
                user=self.actor.get('user'), anon_key=self.actor.get('anon_key'),
                replay_slot=self.actor.get('replay_slot'),
                now=attempt.time,
            )
        with timing_phase(self.timing_request, 'attempt_persist'):
            attempt.save()
            if self.attempt_created_callback is not None:
                self.attempt_created_callback(attempt)
            row.state = dump_state(state)
            row.last_attempt = attempt
            row.save(update_fields=['state', 'last_attempt', 'updated_at'])

    def saved(self, state: dict, status: str, **fields) -> dict[str, Any]:
        with timing_phase(self.timing_request, 'response_payload'):
            out = self.view(state, status, **fields)
        if state.get('won'):
            out['status'] = 'won'
            return self.solved(out)
        return out


def _begin_move(
    *, game, task, user, anon_key, replay_slot, number, share_host, play_path,
    submitted_by_profile_id, attempt_created_callback, timing, timing_request,
):
    """Return (move, None), or (None, error response) when the move cannot start."""
    with timing_phase(timing_request, 'puzzle_load'):
        payload = puzzle_from_task(task)
    if payload is None:
        return None, {'status': 'error', 'error': 'Пазл не настроен'}
    actor = _actor_filters(user=user, anon_key=anon_key, replay_slot=replay_slot)
    if actor is None:
        return None, {
            'status': 'error',
            'error': 'Нужен пользователь',
            **public_payload(default_state(), payload, task=task),
        }
    return _Move(
        game=game, task=task, payload=payload, actor=actor,
        number=number if number is not None else '',
        share_host=share_host, play_path=play_path,
        submitted_by_profile_id=submitted_by_profile_id,
        attempt_created_callback=attempt_created_callback,
        timing=timing, timing_request=timing_request,
    ), None


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
    submitted_by_profile_id: int | None = None,
    attempt_created_callback=None,
    timing=None,
    replay_slot=None,
    timing_request=None,
) -> dict[str, Any]:
    """Open every lemma a guessed word matches. ``timing``: GameplayTimingSession."""
    move, error = _begin_move(
        game=game, task=task, user=user, anon_key=anon_key, replay_slot=replay_slot,
        number=number, share_host=share_host, play_path=play_path,
        submitted_by_profile_id=submitted_by_profile_id,
        attempt_created_callback=attempt_created_callback,
        timing=timing, timing_request=timing_request,
    )
    if error is not None:
        return error
    payload = move.payload

    normalized = normalize_surface(word).strip(_GUESS_EDGE_PUNCTUATION)
    if not normalized or not is_guessable_word(normalized):
        return move.view(
            move.read_state(), 'invalid',
            error=(
                'Вводите части слова через дефис по отдельности'
                if '-' in normalized else 'Введите одно слово (буквы/цифры)'
            ),
        )

    state = move.read_state()
    already_won = move.already_won(state)
    if already_won is not None:
        return already_won

    # Function words are rendered as open from the start and are not part of
    # the guessable content-token set. Reject them before touching the actor
    # state so they never consume an attempt or create a history row.
    if (
        _is_initially_open_word(normalized)
        and not _has_closed_exact_target(payload, normalized)
    ):
        return move.view(
            state, 'initially_open', hits=0, newly_revealed=[], guess_word=normalized,
            error='Это слово уже открыто изначально',
        )

    with timing_phase(timing_request, 'guess_normalize'):
        guess_lemma = lemma_of(normalized)

    duplicate = _duplicate_guess(move, state, normalized)
    if duplicate is not None:
        return duplicate

    move.check_timing()
    with timing_phase(timing_request, 'lexical_analysis'):
        from games.puzzles.censorly.text.flags import lexical_resolver_enabled
        lexical_report = None
        if lexical_resolver_enabled():
            from games.puzzles.censorly.lexical.match import analyze_guess
            cache_key = _lexical_cache_key(task)
            tokens = chain(
                payload.get('title_tokens') or (),
                payload.get('body_tokens') or (),
            )
            matched, lexical_report = analyze_guess(
                tokens, normalized, cache_key=cache_key,
            )
        else:
            matched = lemmas_matching_guess(payload, guess_lemma, normalized)
    return _save_guess(
        move, normalized=normalized, guess_lemma=guess_lemma, matched=matched,
        lexical_report=lexical_report, user=user,
    )


def _duplicate_guess(move: _Move, state: dict, normalized: str) -> dict[str, Any] | None:
    if normalized not in {g.get('word') for g in state.get('guesses') or []}:
        return None
    return move.view(
        state, 'duplicate', guess_word=normalized,
        active_guess_index=_history_index_for_guess(state, move.payload, word=normalized),
        error='Это слово уже вводили',
    )


@transaction.atomic
def _save_guess(move: _Move, *, normalized, guess_lemma, matched, lexical_report, user):
    """Lock and persist a guess after its CPU-heavy lexical analysis."""
    payload = move.payload
    row, state = move.lock_state()
    already_won = move.already_won(state)
    if already_won is not None:
        return already_won
    duplicate = _duplicate_guess(move, state, normalized)
    if duplicate is not None:
        return duplicate

    already_revealed = set(state.get('revealed_lemmas') or [])
    new_matched = matched - already_revealed
    hits = 0
    newly: list[int] = []
    primary_lemma = ''
    if new_matched:
        first_lemma, token_ids = _matching_token_ids(payload, new_matched)
        primary_lemma = guess_lemma if guess_lemma in new_matched else first_lemma
        for lemma_ids in token_ids.values():
            hits += len(lemma_ids)
            newly.extend(lemma_ids)
        # A guess can open several linked lemmas; token IDs follow article
        # reading order, while ``new_matched`` is a set.
        newly.sort()
    elif matched:
        # Already-open lemma (other surface form): do not consume an attempt.
        _first_lemma, token_ids = _matching_token_ids(payload, matched)
        out = move.view(
            state, 'already_open', hits=0, newly_revealed=[], guess_word=normalized,
            active_token_ids=sorted({
                token_id for lemma_ids in token_ids.values() for token_id in lemma_ids
            }),
            error='Уже открыто',
        )
        out['active_guess_index'] = _history_index_for_guess(
            state, payload, target_lemmas=matched, task=move.task,
            public_guesses=out.get('guesses'),
        )
        return out

    revealed = already_revealed | new_matched
    state['revealed_lemmas'] = sorted(revealed)
    state['guesses'] = list(state.get('guesses') or []) + [{
        'word': normalized,
        'lemma': primary_lemma or guess_lemma,
        'hits': hits,
    }]
    state['last_lemma'] = primary_lemma if hits else ''
    state['won'] = _title_won(payload, revealed)
    move.save(row, state, text=normalized, status='Partial' if hits else 'Wrong')
    _record_lexical_decision(
        user=user, game=move.game, task=move.task, guess=normalized, payload=payload,
        report=lexical_report,
    )
    return move.saved(
        state, 'hit' if hits else 'miss',
        hits=hits, newly_revealed=newly, guess_word=normalized,
    )


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
    submitted_by_profile_id: int | None = None,
    attempt_created_callback=None,
    timing=None,
    replay_slot=None,
    timing_request=None,
) -> dict[str, Any]:
    """Reveal one non-title content lemma for −1 point. ``timing``: GameplayTimingSession."""
    move, error = _begin_move(
        game=game, task=task, user=user, anon_key=anon_key, replay_slot=replay_slot,
        number=number, share_host=share_host, play_path=play_path,
        submitted_by_profile_id=submitted_by_profile_id,
        attempt_created_callback=attempt_created_callback,
        timing=timing, timing_request=timing_request,
    )
    if error is not None:
        return error
    payload = move.payload

    tok = token_by_id(payload, int(token_id))
    if tok is None or tok.get('kind') != 'content':
        return {'status': 'error', 'error': 'Выберите скрытое слово'}
    lemma = tok.get('lemma') or ''
    if not lemma:
        return {'status': 'error', 'error': 'Нечего открывать'}
    if lemma in title_opening_lemmas(payload):
        return {
            'status': 'error',
            'error': 'Нельзя открывать слова, связанные со словами из названия',
        }

    unchanged = _unchanged_hint(move, move.read_state(), tok, lemma)
    if unchanged is not None:
        return unchanged
    move.check_timing()
    return _save_hint(move, tok, lemma)


def _unchanged_hint(move: _Move, state: dict, tok: dict, lemma: str) -> dict[str, Any] | None:
    """Answer a hint that would open nothing."""
    already_won = move.already_won(state)
    if already_won is not None:
        return already_won
    if lemma in set(state.get('revealed_lemmas') or []):
        error = 'Слово уже открыто'
    elif (
        tok.get('initially_open') is not False
        and _is_initially_open_word(str(tok.get('surface') or ''))
    ):
        error = 'Слово уже открыто изначально'
    else:
        return None
    return move.view(
        state, 'already_open',
        active_token_ids=newly_revealed_ids(move.payload, lemma), error=error,
    )


@transaction.atomic
def _save_hint(move: _Move, tok: dict, lemma: str):
    row, state = move.lock_state()
    unchanged = _unchanged_hint(move, state, tok, lemma)
    if unchanged is not None:
        return unchanged

    revealed = set(state.get('revealed_lemmas') or [])
    revealed.add(lemma)
    state['revealed_lemmas'] = sorted(revealed)
    state['last_lemma'] = lemma
    state['hints_taken'] = hint_count(state) + 1
    state['hinted_lemmas'] = sorted(set(state.get('hinted_lemmas') or []) | {lemma})
    state['won'] = _title_won(move.payload, revealed)
    move.save(row, state, text=f'#hint:{lemma}', status='Partial')
    return move.saved(
        state, 'hint',
        newly_revealed=newly_revealed_ids(move.payload, lemma), hint_lemma=lemma,
    )


def get_task_for_number(game: Game, number: int | str):
    from games.models import GameTaskGroup

    link = (
        GameTaskGroup.objects.filter(game=game, number=str(number), is_deferred=False)
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
    from games.daily.projection import mark_projection_dirty
    mark_projection_dirty(game, task.task_group, full=True)
    n_attempts = attempt_qs.count()
    chain_qs.delete()
    attempt_qs.delete()
    from games.daily.completion.targeted_completion_reconciliation import reconcile_task_group_actors
    reconcile_task_group_actors(
        game_id=game.pk,
        task_group_id=task.task_group_id,
        actor_keys={(actor['team'].pk if actor['team'] else None,
                     actor['user'].pk if actor['user'] else None,
                     actor['anon_key'])},
    )
    return n_attempts


@dataclass(frozen=True)
class CensorlyMoveRefusal:
    error: str


def posted_guess(body, post):
    body = body or {}
    post = post or {}
    return body.get('word') or body.get('guess') or post.get('word') or ''


def posted_hint_token(body):
    """The hint token, or a refusal when the post does not name one."""
    try:
        return int((body or {}).get('token_id'))
    except (TypeError, ValueError):
        return CensorlyMoveRefusal('Укажите token_id')


def apply_posted_guess(body, post, **play_kwargs):
    return apply_guess(word=posted_guess(body, post), **play_kwargs)


def apply_posted_hint(body, **play_kwargs):
    token_id = posted_hint_token(body)
    if isinstance(token_id, CensorlyMoveRefusal):
        return token_id
    return apply_hint(token_id=token_id, **play_kwargs)


class CensorlyPuzzle(Puzzle):
    task_type = 'censorly'

    posted_guess = staticmethod(posted_guess)
    posted_hint_token = staticmethod(posted_hint_token)
    apply_posted_guess = staticmethod(apply_posted_guess)
    apply_posted_hint = staticmethod(apply_posted_hint)
