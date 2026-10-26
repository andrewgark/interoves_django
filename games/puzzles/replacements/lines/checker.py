"""Replacements-by-lines attempt checker."""

import json

from games.puzzles.core.check_runtime import BaseChecker, CheckResult
from games.puzzles.replacements.lines import (
    parse_replacements_checker_json_lines,
    parse_replacements_lines_text,
    replacements_solved_slots_from_state,
    replacements_state_payload,
)
from games.results.util import clean_text


class ReplacementsLinesChecker(BaseChecker):
    """Проверяет одну строчку: attempt.text = JSON {"line_index": int, "answers": [str, ...]}."""

    def __init__(self, data, last_attempt_state=None):
        self._raw_checker_data = data or ''
        self.last_state = None
        if last_attempt_state:
            try:
                self.last_state = json.loads(last_attempt_state)
            except (ValueError, TypeError):
                self.last_state = None

    def _resolve_answer_rows(self, attempt):
        """(canonical_per_line, accept_lists_per_line) или (None, None) если нечего проверять."""
        raw = (self._raw_checker_data or '').strip()
        if raw:
            json_rows = parse_replacements_checker_json_lines(raw)
            if json_rows:
                return json_rows
        task = getattr(attempt, 'task', None) if attempt else None
        if task and task.text:
            answer_text = (getattr(task, 'checker_data', None) or '').strip() or None
            parsed = parse_replacements_lines_text(task.text, answer_text)
            canonical_rows = parsed.get('answers', [])
            accept_rows = parsed.get('answer_accept') or []
            if canonical_rows and not accept_rows:
                accept_rows = [[[c] for c in row] for row in canonical_rows]
            if canonical_rows:
                return canonical_rows, accept_rows
        return None, None

    def check(self, text, attempt):
        canonical_rows, accept_rows = self._resolve_answer_rows(attempt)
        if not canonical_rows:
            return CheckResult('Wrong', 'Pending', 0, comment='Нет данных для проверки')
        try:
            payload = json.loads(text)
            line_index = int(payload.get('line_index', 0))
            user_answers = payload.get('answers', [])
        except (ValueError, TypeError):
            return CheckResult('Wrong', 'Pending', 0, comment='Неверный формат ответа')
        if line_index < 0 or line_index >= len(canonical_rows):
            return CheckResult('Wrong', 'Pending', 0, comment='Неверный номер строки')
        correct = canonical_rows[line_index]
        opts_row = accept_rows[line_index] if line_index < len(accept_rows) else [[c] for c in correct]

        parsed = {'answers': canonical_rows}
        solved_slots = replacements_solved_slots_from_state(self.last_state, parsed)
        if len(user_answers) > len(correct):
            preserved = replacements_state_payload(solved_slots, parsed)
            already_complete = len(preserved['solved_lines']) >= len(canonical_rows)
            status = 'Ok' if already_complete else ('Partial' if preserved['solved_lines'] else 'Wrong')
            return CheckResult(
                status,
                status if status == 'Ok' else 'Pending',
                preserved['total'],
                state=json.dumps(preserved, ensure_ascii=False),
                comment='Слишком много ответов для строки',
            )
        line_slots = solved_slots.setdefault(line_index, set())
        added_slot = False
        submitted_wrong_unsolved = False

        for slot_index, user_answer in enumerate(user_answers[:len(correct)]):
            if slot_index in line_slots:
                continue
            if not str(user_answer).strip():
                continue
            opts = opts_row[slot_index] if slot_index < len(opts_row) else [correct[slot_index]]
            if any(clean_text(user_answer) == clean_text(option) for option in opts):
                line_slots.add(slot_index)
                added_slot = True
            else:
                submitted_wrong_unsolved = True

        new_state = replacements_state_payload(solved_slots, parsed)
        complete = len(new_state['solved_lines']) >= len(canonical_rows)
        if complete:
            status = 'Ok'
            tournament_status = 'Ok'
        elif new_state['solved_lines'] or added_slot:
            status = 'Partial'
            tournament_status = 'Partial'
        else:
            status = 'Wrong'
            supplied_all_slots = len(user_answers) >= len(correct)
            has_empty = len(user_answers) < len(correct) or any(
                not str(value).strip() for value in user_answers[:len(correct)]
            )
            tournament_status = 'Pending' if supplied_all_slots and submitted_wrong_unsolved and not has_empty else status

        return CheckResult(
            status,
            tournament_status,
            new_state['total'],
            state=json.dumps(new_state, ensure_ascii=False),
        )
