"""Presentation contracts for task cards in the new UI."""

import json


def task_ui_descriptor(
    task, *, rld=None, rd=None, wall_meta=None, ws=None, gp=None,
    board_context_key=None, body_template_override=None,
    body_wrapper_override=None, show_attempts_override=None,
    show_answer_override=None,
):
    body_templates = {
        'wall': 'task-content/task-wall.html',
        'replacements_lines': 'task-content/task-replacements-lines.html',
        'raddle': 'task-content/task-raddle.html',
        'word_salad': 'task-content/task-word-salad.html',
        'grid-puzzle': 'new/task-content/task-grid-puzzle.html',
        'proportions': 'new/task-content/task-proportions.html',
        'default': 'new/task-content/task-default.html',
        'autohint': 'new/task-content/task-default.html',
        'with_tag': 'new/task-content/task-default.html',
        'distribute_to_teams': 'new/task-content/task-default.html',
    }
    body_template = body_template_override or body_templates.get(task.task_type)
    body_error = ''
    if task.task_type == 'replacements_lines' and (not rld or not rld.get('n_lines')):
        body_template = None
        body_error = 'Не получилось показать это задание. Обновите страницу или напишите о проблеме.'
    elif task.task_type == 'raddle' and not rd:
        body_template = None
        body_error = 'Не получилось показать это задание. Обновите страницу или напишите о проблеме.'
    elif task.task_type == 'word_salad' and not ws:
        body_template = None
        body_error = 'Не получилось показать это задание. Обновите страницу или напишите о проблеме.'
    elif task.task_type == 'grid-puzzle' and not gp:
        body_template = None
        body_error = 'Не получилось показать это задание. Обновите страницу или напишите о проблеме.'
    if rld:
        base_max = rld['max_points_total']
    elif rd:
        base_max = rd['max_points_total']
    elif wall_meta:
        base_max = wall_meta['total']
    elif task.task_type == 'word_salad':
        base_max = task.get_results_max_points()
    else:
        base_max = task.get_points()
    attempts_hidden = {'replacements_lines', 'alphabetty', 'word_salad'}
    answer_hidden = {'replacements_lines', 'raddle', 'alphabetty', 'word_salad'}
    body_wrapper = task.task_type in {'wall', 'replacements_lines', 'raddle', 'word_salad'}
    if body_wrapper_override is not None:
        body_wrapper = body_wrapper_override
    show_attempts = task.task_type not in attempts_hidden
    if show_attempts_override is not None:
        show_attempts = show_attempts_override
    show_answer = task.task_type not in answer_hidden
    if show_answer_override is not None:
        show_answer = show_answer_override
    return {
        'body_template': body_template,
        'body_error': body_error,
        'board_context_key': board_context_key,
        'body_wrapper': body_wrapper,
        'base_max': base_max,
        'max_points_title': wall_meta.get('title', '') if wall_meta else '',
        'show_attempts': show_attempts,
        'show_answer': show_answer,
        'unsupported': body_template is None,
        'unsupported_label': 'Не получилось показать это задание. Обновите страницу или напишите о проблеме.',
    }


def wall_ui_context(task, attempts_info, mode):
    if task.task_type != 'wall':
        return None
    from games.templatetags.filters import attempts_with_status
    wall = task.get_wall()
    attempts = list(attempts_info.attempts if attempts_info else [])
    counters = wall.get_n_max_attempts_dict(attempts)
    word_items = []
    explanation_items = {}
    for item in attempts_with_status(attempts):
        attempt = item['attempt']
        try:
            payload = json.loads(attempt.text)
            state = json.loads(attempt.state or '{}')
        except (TypeError, ValueError):
            continue
        if payload.get('stage') == 'cat_words':
            guessed_words = state.get('guessed_words') or []
            slot = wall._cat_words_slot_index(
                len(guessed_words), after_ok=state.get('last_attempt', {}).get('status') == 'Ok'
            )
            word_items.append({**item, 'stage_slot': slot})
        elif payload.get('stage') == 'cat_explanation':
            category = next(
                (i for i, words in enumerate(state.get('guessed_words') or [])
                 if words == payload.get('words')),
                None,
            )
            if category is not None:
                explanation_items.setdefault(category, []).append(item)
    last_state = {}
    if attempts_info and attempts_info.last_attempt:
        try:
            last_state = json.loads(attempts_info.last_attempt.state or '{}')
        except (TypeError, ValueError):
            pass
    active_slot = min(len(last_state.get('guessed_words') or []), max(0, len(wall.max_attempts) - 1))
    active_counter = counters['cat_words'][active_slot]
    return {
        'word_attempts': word_items,
        'explanation_attempts': explanation_items,
        'word_attempt_count': active_counter['n_attempts'],
        'word_attempt_limit': active_counter['max_attempts'],
        'word_attempt_slot': active_slot,
        'word_attempt_total': len(word_items),
        'explanation_total': sum(len(rows) for rows in explanation_items.values()),
        'explanation_limit': task.get_max_attempts(),
        'mode': mode,
    }
