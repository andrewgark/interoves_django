"""Лесенка: one submitted word. The HTTP view still saves the attempt and renders HTML."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from games.puzzles.core.check_runtime import BaseChecker, CheckResult
from games.puzzles.core.primitives import DuplicateVerdict
from games.puzzles.core.quota import json_field_quota
from games.raddle import (
    dump_raddle_state,
    load_raddle_state,
    merge_raddle_ui_payload,
    parse_raddle_data,
    playable_word_indices,
    raddle_blocks_as_duplicate,
    resolve_assist_tiers,
    serialize_raddle_attempt_text,
    word_length_matches,
    word_matches,
    word_solve_credit,
)


@dataclass(frozen=True)
class RaddleMove:
    word_index: int
    word: str
    text: str


@dataclass(frozen=True)
class RaddleMoveRefusal:
    status: str


def parse_move(post):
    """Read a ladder POST. Refusal statuses are the existing JSON codes."""
    try:
        word_index = int(post.get('word_index', 0))
    except (TypeError, ValueError):
        return RaddleMoveRefusal('invalid_form')
    word = (post.get('word') or '').strip()
    if not word:
        return RaddleMoveRefusal('empty')
    return RaddleMove(
        word_index,
        word,
        serialize_raddle_attempt_text(word_index, word),
    )


def assist_block(state, n_words, word_index, tier):
    """Status when a ladder hint must not be written. None means the view may write it."""
    state = state or {}
    if word_index in set(state.get('solved_indices') or []):
        return 'already_solved'
    if word_index not in playable_word_indices(state, n_words):
        return 'not_playable'
    current_tier = resolve_assist_tiers(state).get(word_index, 0)
    if int(tier) == 2 and current_tier < 1:
        return 'need_clue_first'
    if int(tier) == 1 and current_tier >= 1:
        return 'duplicate'
    return None


@dataclass(frozen=True)
class RaddleUiAcceptance:
    stale: bool
    state: dict
    revision: int


def accept_ui_draft(
    stored, n_words, *, stored_revision, requested_revision,
    drafts_patch, clue_marks_patch,
):
    """Keep the stored draft when the client is not newer. Otherwise merge the patch."""
    stored = {
        'drafts': dict((stored or {}).get('drafts') or {}),
        'clue_marks': dict((stored or {}).get('clue_marks') or {}),
    }
    stored_revision = int(stored_revision or 0)
    stale = requested_revision is not None and requested_revision <= stored_revision
    if stale:
        return RaddleUiAcceptance(True, stored, stored_revision)
    merged = merge_raddle_ui_payload(
        stored, n_words, drafts_patch=drafts_patch, clue_marks_patch=clue_marks_patch,
    )
    return RaddleUiAcceptance(
        False,
        merged,
        max(stored_revision + 1, requested_revision or 0),
    )


def posted_word_index(post, default=-1):
    try:
        return int(post.get('word_index', default))
    except (TypeError, ValueError):
        return default


def attempt_limit_message(attempt, revision_attempts, task, team, *, attempts=None, chain_state=None):
    """Tournament quota for one ladder word. None means the word still has tries."""
    return json_field_quota(
        attempt, revision_attempts, task, team,
        field='word_index', label='word',
    )


def blocks_duplicate(attempt_text, other_text, *, task, state_raw, other_attempt=None):
    if not raddle_blocks_as_duplicate(
        attempt_text,
        other_text,
        task=task,
        state_raw=state_raw,
        other_attempt=other_attempt,
    ):
        return DuplicateVerdict(False)
    return DuplicateVerdict(True)


def chain_state(task, team, user, anon_key, game, current_mode, replay_slot=None):
    """Current ladder state for an actor: chain row, else the latest attempt."""
    from games.models import Attempt, ChainTaskState

    parsed = parse_raddle_data(task)
    if not parsed:
        return None, None
    n_words = parsed['n_words']
    chain_row = ChainTaskState.objects.filter(
        team=team, user=user, anon_key=anon_key,
        task=task, game=game, game_mode=current_mode,
        replay_slot=replay_slot,
    ).first()
    if chain_row and chain_row.state:
        return parsed, load_raddle_state(chain_row.state, n_words)
    attempts = Attempt.manager.get_attempts(
        team, task, mode=current_mode, user=user, anon_key=anon_key, game=game,
        replay_slot=replay_slot,
    )
    for previous in reversed(attempts):
        if previous.state:
            return parsed, load_raddle_state(previous.state, n_words)
    return parsed, load_raddle_state(None, n_words)


def stale_payload(
    task, team, user, anon_key, game, current_mode, word_index, replay_slot=None,
):
    """Payload for a word the open page can no longer submit. None means accept it.

    HTML refresh stays in the view: this only decides that the client is stale.
    """
    parsed, state = chain_state(
        task, team, user, anon_key, game, current_mode, replay_slot=replay_slot,
    )
    if not parsed or state is None:
        return None
    solved = set(state.get('solved_indices') or [])
    playable = playable_word_indices(state, parsed['n_words'])
    # Already solved words stay on the duplicate_solved / needs_sync paths.
    if word_index not in playable and word_index not in solved:
        return {
            'status': 'ok',
            'task_id': task.id,
            'raddle_correct': False,
            'raddle_needs_sync': True,
            'raddle_stale_ui': True,
            'raddle_word_index': word_index,
        }
    return None


def duplicate_fields(task, word_index, *, team, user, anon_key, game, current_mode):
    """Duplicate POST: tell the client whether that word is already solved."""
    from games.models import ChainTaskState

    parsed = parse_raddle_data(task)
    solved = False
    if parsed and word_index >= 0:
        chain_row = ChainTaskState.objects.filter(
            team=team, user=user, anon_key=anon_key,
            task=task, game=game, game_mode=current_mode,
        ).first()
        state = load_raddle_state(
            chain_row.state if chain_row else None,
            parsed['n_words'],
        )
        solved = word_index in set(state.get('solved_indices') or [])
    return {
        'raddle_word_index': word_index,
        'raddle_duplicate_solved': solved,
    }


def write_move_result(result, task, attempt, word_index, word):
    """Partial means the ladder advanced earlier, not that this word was correct."""
    raddle_correct = False
    try:
        state = json.loads(attempt.state or '{}')
        solved = set(state.get('solved_indices') or [])
        parsed = parse_raddle_data(task)
        if word_index in solved and parsed:
            raddle_correct = word_matches(word, parsed['word_accept'][word_index])
    except (ValueError, TypeError, IndexError, KeyError):
        pass
    result['raddle_correct'] = raddle_correct
    result['raddle_word_index'] = word_index
    if not result['raddle_correct']:
        try:
            state = json.loads(attempt.state or '{}')
            if word_index in set(state.get('solved_indices') or []):
                result['raddle_needs_sync'] = True
        except (ValueError, TypeError):
            pass
    return result


def include_task_html(result):
    """Wrong ladder words update on the client. A correct or stale word needs HTML."""
    return bool(result.get('raddle_correct') or result.get('raddle_needs_sync'))


class RaddleChecker(BaseChecker):
    """Проверяет одно слово лестницы: attempt.text = JSON {"word_index": int, "word": str}."""

    def __init__(self, data, last_attempt_state=None):
        self._raw_checker_data = data or ''
        self.last_state = None
        if last_attempt_state:
            try:
                self.last_state = json.loads(last_attempt_state)
            except (ValueError, TypeError):
                self.last_state = None

    def _parsed(self, attempt):
        task = getattr(attempt, 'task', None) if attempt else None
        if not task:
            return None
        return parse_raddle_data(task)

    def check(self, text, attempt):
        parsed = self._parsed(attempt)
        if not parsed:
            return CheckResult('Wrong', 'Pending', 0, comment='Нет данных для проверки')
        n = parsed['n_words']
        state = load_raddle_state(
            json.dumps(self.last_state) if self.last_state else None,
            n,
        )
        try:
            payload = json.loads(text)
            word_index = int(payload.get('word_index', -1))
            user_word = str(payload.get('word', '') or '').strip()
        except (ValueError, TypeError):
            return CheckResult('Wrong', 'Pending', 0, comment='Неверный формат ответа')
        if not user_word:
            return CheckResult(
                'Wrong', 'Pending', 0,
                comment='Пустой ответ',
                state=json.dumps(dump_raddle_state(state, n), ensure_ascii=False),
            )
        if word_index < 0 or word_index >= n:
            return CheckResult(
                'Wrong', 'Pending', 0,
                comment='Неверный индекс слова',
                state=json.dumps(dump_raddle_state(state, n), ensure_ascii=False),
            )
        if word_index in set(state.get('solved_indices') or []):
            return CheckResult(
                'Wrong', 'Pending', 0,
                comment='Это слово уже решено',
                state=json.dumps(dump_raddle_state(state, n), ensure_ascii=False),
            )
        playable = playable_word_indices(state, n)
        if word_index not in playable:
            return CheckResult(
                'Wrong', 'Pending', 0,
                comment='Сейчас можно сдавать только крайние нерешённые слова',
                state=json.dumps(dump_raddle_state(state, n), ensure_ascii=False),
            )
        mask = parsed['masks'][word_index]
        if not word_length_matches(user_word, mask):
            return CheckResult(
                'Wrong', 'Pending', 0,
                comment='Длина слова не совпадает с маской',
                state=json.dumps(dump_raddle_state(state, n), ensure_ascii=False),
            )
        accept = parsed['word_accept'][word_index]
        is_correct = word_matches(user_word, accept)
        solved = set(state.get('solved_indices') or [])
        used_hints = set(state.get('used_hints') or [])
        try:
            total = Decimal(str(state.get('total', 0) or 0))
        except (ArithmeticError, ValueError, TypeError):
            total = Decimal('0')
        assist_tiers = resolve_assist_tiers(state)
        assist_cfg = (parsed.get('assist') or {}) if parsed else {}
        if is_correct:
            solved_before = set(solved)
            solved.add(word_index)
            n_hints = len(parsed['hints'])
            if (word_index - 1) in solved_before and 0 <= word_index - 1 < n_hints:
                used_hints.add(word_index - 1)
            if (word_index + 1) in solved_before and 0 <= word_index < n_hints:
                used_hints.add(word_index)
            if word_index not in (0, n - 1):
                tier = assist_tiers.get(word_index, 0)
                total += word_solve_credit(tier, assist_cfg)
        middle_total = max(0, n - 2)
        solved_middle = len(solved) - (2 if n >= 2 else len(solved))
        if middle_total and solved_middle >= middle_total:
            status = 'Ok'
            tournament_status = 'Ok'
        else:
            status = 'Partial' if solved_middle > 0 else 'Wrong'
            tournament_status = status if is_correct else 'Pending'
        new_state = dump_raddle_state(
            {
                'solved_indices': sorted(solved),
                'used_hints': sorted(used_hints),
                'assist_tier': state.get('assist_tier') or {},
                'total': float(total),
                'drafts': state.get('drafts') or {},
                'clue_marks': state.get('clue_marks') or {},
            },
            n,
        )
        return CheckResult(
            status, tournament_status, total,
            state=json.dumps(new_state, ensure_ascii=False),
        )
