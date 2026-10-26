"""Салатик: one submitted path or hint. Sync of side finds stays a view, it renders HTML."""

from __future__ import annotations

import json
from dataclasses import dataclass

from games.puzzles.core.check_runtime import BaseChecker, CheckResult
from games.puzzles.core.primitives import same_text_verdict
from games.puzzles.salad.rules import (
    EXTRA_FOUND_COMMENT,
    EXTRA_NOT_FOUND_COMMENT,
    RARE_FOUND_COMMENT,
    append_unique_word,
    dump_state,
    extra_found_word,
    extra_found_word_from_attempt,
    load_state,
    neighbours,
    normalize_word,
    parse_task_payload,
    path_word_from_attempt,
    removable_cells,
    score_for_state,
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


class WordSaladChecker(BaseChecker):
    """Проверяет JSON-путь по сетке и хранит найденные слова в chain state."""

    def __init__(self, data, last_attempt_state=None):
        self.grid, self.words, self.rare_words = parse_task_payload(data, '')
        self.last_state = load_state(last_attempt_state)

    def check(self, text, attempt):
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return CheckResult('Wrong', 'Pending', 0, comment='Неверный формат пути')
        if not isinstance(payload, dict):
            return CheckResult('Wrong', 'Pending', 0, comment='Неверный формат пути')
        state = load_state(self.last_state)
        solved = set(state.get('solved_indices') or [])
        hint_counts = dict(state.get('hint_counts') or {})
        active = set(state.get('active', []))
        found_rare = set(state.get('found_rare') or [])
        found_rare_words = list(state.get('found_rare_words') or [])
        found_extra = list(state.get('found_extra') or [])

        def play_state():
            return {
                'solved_indices': sorted(solved),
                'hints': sorted(hint_counts),
                'hint_counts': hint_counts,
                'active': sorted(active),
                'found_rare': sorted(found_rare),
                'found_rare_words': found_rare_words,
                'found_extra': found_extra,
            }

        action = (payload.get('action') or 'solve').strip().lower()
        if action == 'hint':
            try:
                word_index = int(payload.get('word_index', -1))
            except (TypeError, ValueError):
                return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Неверная подсказка')
            if not (0 <= word_index < len(self.words)):
                return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Неверная подсказка')
            if word_index in solved:
                return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Слово уже открыто')
            current_count = int(hint_counts.get(word_index, 0) or 0)
            word_length = len(normalize_word(self.words[word_index]))
            if current_count >= word_length:
                return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Все буквы уже раскрыты')
            requested_number = payload.get('hint_number')
            if requested_number is not None:
                try:
                    requested_number = int(requested_number)
                except (TypeError, ValueError):
                    return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Неверная подсказка')
                if requested_number != current_count + 1:
                    return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Устаревшая подсказка')
            hint_counts[word_index] = current_count + 1
            next_state = play_state()
            tournament_status = 'Partial' if solved else 'Wrong'
            return CheckResult('Partial', tournament_status, score_for_state(next_state), dump_state(next_state))
        try:
            path = [int(value) for value in payload.get('path', [])]
        except (TypeError, ValueError):
            return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Неверный формат пути')
        if not path or len(path) != len(set(path)) or any(i not in active for i in path):
            return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Недопустимый путь')
        if any(b not in set(neighbours(a)) for a, b in zip(path, path[1:])):
            return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Буквы не соседние')
        written = ''.join(self.grid[i] for i in path)
        already = next((i for i, word in enumerate(self.words) if normalize_word(word) == written), None)
        if already is not None and already in solved:
            return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Слово уже открыто')
        match = next((i for i, word in enumerate(self.words)
                      if i not in solved and normalize_word(word) == written), None)
        if match is not None:
            solved.add(match)
            while True:
                removable = removable_cells(self.grid, self.words, active, solved)
                if not removable:
                    break
                active.discard(removable[0])
            next_state = play_state()
            status = 'Ok' if len(solved) == len(self.words) else 'Partial'
            return CheckResult(status, status, score_for_state(next_state), dump_state(next_state))
        already_rare = next(
            (i for i, word in enumerate(self.rare_words) if normalize_word(word) == written),
            None,
        )
        if already_rare is not None and already_rare in found_rare:
            return CheckResult(
                'Wrong',
                'Pending',
                score_for_state(state),
                dump_state(play_state()),
                comment='Слово уже открыто',
            )
        rare_match = next(
            (
                i for i, word in enumerate(self.rare_words)
                if i not in found_rare and normalize_word(word) == written
            ),
            None,
        )
        if rare_match is not None:
            found_rare.add(rare_match)
            append_unique_word(found_rare_words, self.rare_words[rare_match])
            next_state = play_state()
            tournament_status = 'Partial' if solved else 'Wrong'
            return CheckResult(
                'Partial',
                tournament_status,
                score_for_state(next_state),
                dump_state(next_state),
                comment=RARE_FOUND_COMMENT,
            )
        extra = extra_found_word(written, self.words, rare_words=self.rare_words)
        if extra:
            if extra in found_extra:
                return CheckResult(
                    'Wrong',
                    'Pending',
                    score_for_state(state),
                    dump_state(play_state()),
                    comment='Слово уже открыто',
                )
            append_unique_word(found_extra, extra)
            next_state = play_state()
            tournament_status = 'Partial' if solved else 'Wrong'
            return CheckResult(
                'Partial',
                tournament_status,
                score_for_state(next_state),
                dump_state(next_state),
                comment=EXTRA_FOUND_COMMENT,
            )
        return CheckResult(
            'Wrong',
            'Pending',
            score_for_state(state),
            dump_state(play_state()),
            comment=EXTRA_NOT_FOUND_COMMENT,
        )
