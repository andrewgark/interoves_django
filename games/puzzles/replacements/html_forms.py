"""HTML-формы: one posted field. The view still saves the attempt and checks the quota."""

from __future__ import annotations

import json

from games.puzzles.core.base import Puzzle
from games.puzzles.core.check_runtime import BaseChecker, CheckResult
from games.puzzles.core.primitives import MoveRefusal, TextMove, same_text_verdict
from games.puzzles.core.progress import _text_tuple
from games.puzzles.core.quota import revision_quota_message
from games.puzzles.replacements.forms import (
    html_forms_answer_matches,
    html_forms_solved_keys_from_state,
    html_forms_state_payload,
    parse_html_forms_data,
)


def parse_move(post):
    """Read an html-forms POST. Statuses are the existing JSON codes."""
    form_key = str(post.get('form_key') or '').strip()
    answer_text = (post.get('text') or '').strip()
    if not form_key:
        return MoveRefusal('invalid_form')
    if not answer_text:
        return MoveRefusal('empty')
    return TextMove(json.dumps(
        {'form_key': form_key, 'text': answer_text},
        ensure_ascii=False,
    ))


def attempt_limit_message(attempt, revision_attempts, task, team, *, attempts=None, chain_state=None):
    """Tournament quota for the whole revision. None means tries remain."""
    return revision_quota_message(attempt, revision_attempts, task, team)


def blocks_duplicate(attempt_text, other_text, *, task, state_raw, other_attempt=None):
    return same_text_verdict(attempt_text, other_text, other_attempt)


def progress_signature(state):
    """Solved form keys. Points are not part of the signature."""
    return ('forms', _text_tuple(state.get('solved_keys')))


def clean_checker_data(task):
    """Reject an html-forms payload that has no answered forms."""
    from django.core.exceptions import ValidationError
    from games.puzzles.replacements.forms import parse_html_forms_data
    if not parse_html_forms_data(task.checker_data):
        raise ValidationError({'checker_data': 'Нужен JSON со списком forms и ответами.'})


def is_complete(task, state_raw):
    """True when every form key is solved."""
    from games.puzzles.replacements.forms import html_forms_is_complete
    return html_forms_is_complete(task, state_raw)


def attempt_answer(attempt):
    """The accepted answer for the submitted form key."""
    try:
        payload = json.loads(attempt.text)
        key = str(payload.get('form_key') or '')
    except (ValueError, TypeError):
        return attempt.task.answer or ''
    from games.puzzles.replacements.forms import parse_html_forms_data
    for form in parse_html_forms_data(attempt.task.checker_data):
        if form['key'] == key:
            return form['answer']
    return '—'


def pretty_text(attempt):
    """List label for one html form, without exposing the private key."""
    from django.utils.html import format_html
    from games.puzzles.replacements.forms import parse_html_forms_data
    try:
        payload = json.loads(attempt.text)
        if not isinstance(payload, dict):
            return '—'
        key = str(payload.get('form_key') or '')
        answer = str(payload.get('text') or '')
        for form in parse_html_forms_data(attempt.task.checker_data):
            if form['key'] == key:
                return format_html('{}: {}', form['placeholder'], answer)
        return format_html('{}', answer) if answer else '—'
    except (ValueError, TypeError):
        return '—'


def state_summary(state):
    """Admin one-line summary of an html-forms chain."""
    solved = len(state.get('solved_keys', []))
    total = state.get('total', '?')
    return '{} forms solved ({} pts)'.format(solved, total)


def results_max_points(task, m):
    """Green-cell maximum: form count times the task multiplier."""
    from games.puzzles.replacements.forms import parse_html_forms_data
    n = len(parse_html_forms_data(task.checker_data))
    if n > 0:
        return m * n
    return m


class HtmlFormsPuzzle(Puzzle):
    task_type = 'html_forms'
    checker_id = 'html_forms'

    parse_move = staticmethod(parse_move)
    attempt_limit_message = staticmethod(attempt_limit_message)
    blocks_duplicate = staticmethod(blocks_duplicate)
    progress_signature = staticmethod(progress_signature)
    clean_checker_data = staticmethod(clean_checker_data)
    is_complete = staticmethod(is_complete)
    attempt_answer = staticmethod(attempt_answer)
    pretty_text = staticmethod(pretty_text)
    state_summary = staticmethod(state_summary)
    results_max_points = staticmethod(results_max_points)


class HtmlFormsChecker(BaseChecker):
    """Checks one embedded form: attempt.text = JSON {"form_key": str, "text": str}."""

    def __init__(self, data, last_attempt_state=None):
        self.forms = parse_html_forms_data(data)
        self.forms_by_key = {form['key']: form for form in self.forms}
        self.solved_keys = html_forms_solved_keys_from_state(last_attempt_state)

    def check(self, text, attempt):
        if not self.forms:
            return CheckResult('Wrong', 'Pending', 0, comment='Нет данных для проверки')
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return CheckResult('Wrong', 'Pending', 0, comment='Неверный формат ответа')
        if not isinstance(payload, dict):
            return CheckResult('Wrong', 'Pending', 0, comment='Неверный формат ответа')
        form_key = str(payload.get('form_key') or '').strip()
        user_answer = str(payload.get('text') or '').strip()
        form = self.forms_by_key.get(form_key)
        if form is None:
            return CheckResult('Wrong', 'Pending', 0, comment='Неверная форма ответа')
        if not user_answer:
            preserved = html_forms_state_payload(self.solved_keys, self.forms)
            status = 'Ok' if preserved['total'] >= preserved['n_forms'] else ('Partial' if preserved['total'] else 'Wrong')
            return CheckResult(
                status,
                status if status == 'Ok' else 'Pending',
                preserved['total'],
                state=json.dumps(preserved, ensure_ascii=False),
                comment='Пустой ответ',
            )
        added = False
        wrong_unsolved = False
        if form_key not in self.solved_keys:
            if html_forms_answer_matches(user_answer, form['answers']):
                self.solved_keys.add(form_key)
                added = True
            else:
                wrong_unsolved = True
        new_state = html_forms_state_payload(self.solved_keys, self.forms)
        complete = new_state['total'] >= new_state['n_forms']
        if complete:
            status = 'Ok'
            tournament_status = 'Ok'
        elif wrong_unsolved:
            status = 'Wrong'
            tournament_status = 'Pending'
        elif added or new_state['total']:
            status = 'Partial'
            tournament_status = 'Partial'
        else:
            status = 'Wrong'
            tournament_status = 'Pending' if wrong_unsolved else 'Wrong'
        return CheckResult(
            status,
            tournament_status,
            new_state['total'],
            state=json.dumps(new_state, ensure_ascii=False),
        )
