"""Tasks whose text or title hides answer fields for other tasks of the group.

``text_with_forms``: the task text contains ``______`` blanks; blank *i* is the
answer field of the (*i* + 1)-th visible task of the group (task 0 is the text
itself). ``LEFT_ANSWERS`` in the text lists answers not guessed yet.

``text_with_forms_in_name`` (task group or game tag): the same blanks in the
title, filled with the answers once solved (blank *i* → *i*-th task).

``should_be_hidden_if_not_solved`` (task tag, list of task ids): the card stays
closed until all referenced tasks are solved.
"""

from django.utils.html import escape

from games.models import Task

BLANK = '______'
LEFT_ANSWERS = 'LEFT_ANSWERS'
HIDDEN_TAG = 'should_be_hidden_if_not_solved'
NAME_TAG = 'text_with_forms_in_name'


def group_tasks(task_group):
    return sorted(task_group.tasks.visible(), key=lambda t: t.key_sort())


def _is_solved(attempts_info_by_task_id, task):
    ai = attempts_info_by_task_id.get(task.id)
    return bool(ai and ai.is_solved())


def _hidden_ref_ids(task):
    ref_ids = (task.tags or {}).get(HIDDEN_TAG)
    return list(ref_ids) if isinstance(ref_ids, (list, tuple)) else []


def support_tasks(tasks):
    """Tasks outside ``tasks`` whose attempts the given cards need to render."""
    seen = {t.id for t in tasks}
    out = []

    def add(task):
        if task.id not in seen:
            seen.add(task.id)
            out.append(task)

    hidden_ref_ids = []
    for t in tasks:
        # A text card fills its blanks, a title with blanks its heading, from the group's tasks.
        if t.task_type == 'text_with_forms' or NAME_TAG in (t.task_group.tags or {}):
            for other in group_tasks(t.task_group):
                add(other)
        hidden_ref_ids.extend(_hidden_ref_ids(t))
    if hidden_ref_ids:
        for ref in Task.objects.visible().filter(id__in=hidden_ref_ids):
            add(ref)
    return out


def _blank_segments(text, numbered_tasks, attempts_info_by_task_id, *, first_task_index):
    """Split ``text`` at blanks: ``{'html': ...}`` parts and ``{'task', 'solved'}`` fields."""
    parts = (text or '').split(BLANK)
    segments = []
    for i, part in enumerate(parts):
        segments.append({'html': part})
        if i == len(parts) - 1:
            break
        n = first_task_index + i
        if n >= len(numbered_tasks):
            segments.append({'html': BLANK})
            continue
        task = numbered_tasks[n]
        segments.append({'task': task, 'solved': _is_solved(attempts_info_by_task_id, task)})
    return segments


def _left_answers_html(tasks, attempts_info_by_task_id):
    left = sorted(
        (t for t in tasks if not _is_solved(attempts_info_by_task_id, t)),
        key=lambda t: t.answer or '',
    )
    return ''.join('- {}<br>'.format(t.answer or '') for t in left)


def text_with_forms_data_by_task_id(tasks, attempts_info_by_task_id) -> dict:
    """``{task_id: {'segments': [...]}}`` for ``text_with_forms`` cards."""
    out = {}
    for t in tasks:
        if t.task_type != 'text_with_forms':
            continue
        numbered = group_tasks(t.task_group)
        segments = _blank_segments(t.text, numbered, attempts_info_by_task_id, first_task_index=1)
        if LEFT_ANSWERS in (t.text or ''):
            left_html = _left_answers_html(numbered[1:], attempts_info_by_task_id)
            for seg in segments:
                if 'html' in seg:
                    seg['html'] = seg['html'].replace(LEFT_ANSWERS, left_html)
        out[t.id] = {'segments': segments}
    return out


def hidden_until_solved_by_task_id(tasks, attempts_info_by_task_id) -> dict:
    """``{task_id: [numbers of tasks still to solve]}`` for closed cards."""
    out = {}
    for t in tasks:
        ref_ids = _hidden_ref_ids(t)
        if not ref_ids:
            continue
        by_id = {r.id: r for r in Task.objects.visible().filter(id__in=ref_ids)}
        refs = [by_id[i] for i in ref_ids if i in by_id]
        unsolved = [r.number for r in refs if not _is_solved(attempts_info_by_task_id, r)]
        if unsolved:
            out[t.id] = unsolved
    return out


def title_with_blanks_html(title, tasks, attempts_info_by_task_id):
    """Title HTML with solved blanks filled in; unsolved ones stay ``______``."""
    parts = []
    for seg in _blank_segments(title, tasks, attempts_info_by_task_id, first_task_index=0):
        if 'html' in seg:
            parts.append(escape(seg['html']))
        elif seg['solved']:
            parts.append('<span class="new-title-blank new-title-blank--solved">{}</span>'.format(
                escape(seg['task'].answer or ''),
            ))
        else:
            parts.append('<span class="new-title-blank">{}</span>'.format(BLANK))
    return ''.join(parts)


def task_group_heading_html(placement, attempts_info_by_task_id):
    """Task-group heading with filled blanks, or None when the name has no forms."""
    if placement is None or NAME_TAG not in (placement.task_group.tags or {}):
        return None
    return title_with_blanks_html(
        placement.name, group_tasks(placement.task_group), attempts_info_by_task_id,
    )
