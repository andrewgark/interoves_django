"""Салатик: one submitted path or hint. Sync of side finds stays a view, it renders HTML."""

from __future__ import annotations

import json
from dataclasses import dataclass

from games.puzzles.core.primitives import same_text_verdict
from games.word_salad import (
    EXTRA_FOUND_COMMENT,
    RARE_FOUND_COMMENT,
    extra_found_word_from_attempt,
    path_word_from_attempt,
)


@dataclass(frozen=True)
class SaladMove:
    action: str
    text: str
    starts_game: bool


@dataclass(frozen=True)
class SaladMoveRefusal:
    status: str


@dataclass(frozen=True)
class SaladSync:
    """The POST asks to sync side finds. That response is still built by the view."""


def parse_move(post):
    action = (post.get('action') or 'solve').strip().lower()
    if action == 'sync_finds':
        return SaladSync()
    if action == 'hint':
        try:
            word_index = int(post.get('word_index', -1))
        except (TypeError, ValueError):
            return SaladMoveRefusal('empty')
        if word_index < 0:
            return SaladMoveRefusal('empty')
        hint_payload = {'action': 'hint', 'word_index': word_index}
        hint_number = post.get('hint_number')
        if hint_number not in (None, ''):
            try:
                hint_payload['hint_number'] = int(hint_number)
            except (TypeError, ValueError):
                return SaladMoveRefusal('empty')
        return SaladMove(action, json.dumps(hint_payload), False)

    path_raw = post.get('path') or post.get('path_json') or '[]'
    if isinstance(path_raw, str):
        try:
            path = json.loads(path_raw)
        except (TypeError, ValueError):
            path = []
    else:
        path = list(path_raw)
    try:
        path = [int(value) for value in path]
    except (TypeError, ValueError):
        return SaladMoveRefusal('empty')
    if not path:
        return SaladMoveRefusal('empty')
    # Unknown actions still submit a solve path. correct_only stays tied to
    # the posted action, which is "solve" only when the client said so.
    return SaladMove(
        action,
        json.dumps({'action': 'solve', 'path': path}),
        True,
    )


def attempt_limit_message(attempt, revision_attempts, task, team, *, attempts=None, chain_state=None):
    """Salad has no tournament quota. Hints are limited by the word itself."""
    return None


def blocks_duplicate(attempt_text, other_text, *, task, state_raw, other_attempt=None):
    """Same posted text is a duplicate, and the previous attempt id stays on it."""
    return same_text_verdict(attempt_text, other_text, other_attempt)


def write_move_result(result, task, attempt, *, attempt_persisted, user, anon_key):
    rare_found = attempt_persisted and (attempt.comment or '') == RARE_FOUND_COMMENT
    extra_found = attempt_persisted and (attempt.comment or '') == EXTRA_FOUND_COMMENT
    result['word_salad_correct'] = bool(attempt_persisted) and not rare_found and not extra_found
    if rare_found:
        written, _words, _rare_words = path_word_from_attempt(task, attempt)
        if written:
            result['word_salad_rare'] = written
        result['word_salad_keep_selection'] = True
    elif extra_found:
        written, _words, _rare_words = path_word_from_attempt(task, attempt)
        if written:
            result['word_salad_extra'] = written
        result['word_salad_keep_selection'] = True
    elif result['word_salad_correct']:
        result['word_salad_keep_selection'] = False
    if not attempt_persisted:
        if attempt.comment:
            result['word_salad_comment'] = attempt.comment
        extra = extra_found_word_from_attempt(
            task, attempt, user=user, anon_key=anon_key,
        )
        if extra:
            result['word_salad_extra'] = extra
    return result


def include_task_html(result):
    """A wrong salad path stays on the client. A credited word needs fresh HTML."""
    return bool(result.get('word_salad_correct'))
