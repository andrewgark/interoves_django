"""HTML-формы: one posted field. The view still saves the attempt and checks the quota."""

from __future__ import annotations

import json

from games.puzzles.base import Puzzle
from games.puzzles.duplicate import same_text_verdict
from games.puzzles.move import MoveRefusal, TextMove
from games.puzzles.progress import _text_tuple
from games.puzzles.quota import revision_quota_message


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
    from games.html_forms import parse_html_forms_data
    if not parse_html_forms_data(task.checker_data):
        raise ValidationError({'checker_data': 'Нужен JSON со списком forms и ответами.'})


def is_complete(task, state_raw):
    """True when every form key is solved."""
    from games.html_forms import html_forms_is_complete
    return html_forms_is_complete(task, state_raw)


def attempt_answer(attempt):
    """The accepted answer for the submitted form key."""
    try:
        payload = json.loads(attempt.text)
        key = str(payload.get('form_key') or '')
    except (ValueError, TypeError):
        return attempt.task.answer or ''
    from games.html_forms import parse_html_forms_data
    for form in parse_html_forms_data(attempt.task.checker_data):
        if form['key'] == key:
            return form['answer']
    return '—'


def pretty_text(attempt):
    """List label for one html form, without exposing the private key."""
    from django.utils.html import format_html
    from games.html_forms import parse_html_forms_data
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
    from games.html_forms import parse_html_forms_data
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
