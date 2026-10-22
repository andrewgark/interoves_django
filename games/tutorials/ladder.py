"""Short Ladder tutorial: a real raddle task played on the production page.

The task lives in its own TaskGroup outside the ladder archive (like a draft
ladder offer), so it is solved through the same templates, JS and
``/send_attempt/`` contract as a daily Ladder. Only the popup layer
(``static/js/ladder_tutorial.js``) is tutorial-specific.
"""

import json

from django.db import transaction


LADDER_TUTORIAL_VERSION = 'v9'
LADDER_TUTORIAL_LABEL = 'ladder_tutorial'
LADDER_TUTORIAL_SEGMENT = 'tutorial'
LADDER_TUTORIAL_TITLE = 'Обучающая Лесенка'

_WORDS = ('ПЕРВАЯ', 'ПЯТАЯ', 'ЗАПЯТАЯ', 'ТОЧКА', 'ЛАСТОЧКА')
_HINTS = (
    'А и Д — это ____ и ... буквы алфавита',
    'Знак препинания, заканчивающийся на ____',
    '... с хвостиком — это ____',
    'Птица, заканчивающаяся на ____',
)


def _checker_payload():
    from games.support.services.ladders import build_checker_payload

    return build_checker_payload(list(_WORDS), list(_HINTS))


def is_ladder_tutorial_task_group(task_group):
    return bool(task_group is not None and getattr(task_group, 'label', None) == LADDER_TUTORIAL_LABEL)


@transaction.atomic
def ensure_ladder_tutorial_task():
    """Return the tutorial Task, creating it or syncing its content with code."""
    from games.models import CheckerType, Task, TaskGroup
    from games.raddle import ensure_raddle_assist_hints

    checker = CheckerType.objects.get(id='raddle')
    task_group = (
        TaskGroup.objects.select_for_update()
        .filter(label=LADDER_TUTORIAL_LABEL).order_by('pk').first()
    )
    if task_group is None:
        task_group = TaskGroup.objects.create(
            label=LADDER_TUTORIAL_LABEL, checker=checker, points=1, max_attempts=3,
        )
    checker_data = json.dumps(_checker_payload(), ensure_ascii=False)
    answer = '\n'.join(_WORDS)
    task = Task.objects.filter(task_group=task_group, number='1').first()
    if task is None:
        task = Task.objects.create(
            task_group=task_group, number='1', task_type='raddle', checker=checker,
            checker_data=checker_data, answer=answer, text='', tags={},
            points=1, max_attempts=None, is_removed=False,
        )
    elif task.checker_data != checker_data or task.answer != answer:
        # Saving semantic fields rotates attempt_revision, so only do it on change.
        task.checker_data = checker_data
        task.answer = answer
        task.save()
    ensure_raddle_assist_hints(task)
    return task


def reset_ladder_tutorial_progress(task, *, team=None, user=None, anon_key=None):
    """Every visit starts the tutorial from a clean board."""
    from games.ladder_offer import reset_raddle_progress

    if team is None and user is None and not anon_key:
        return 0
    return reset_raddle_progress(task=task, team=team, user=user, anon_key=anon_key)


def ladder_tutorial_payload(task):
    """Data for the popup layer; the board itself comes from the real page."""
    from django.urls import reverse

    return {
        'version': LADDER_TUTORIAL_VERSION,
        'game': 'ladder',
        'task_id': task.id,
        'today_url': reverse('new_ladder_today'),
        'words': _WORDS,
        'lengths': tuple(len(word) for word in _WORDS),
        'steps': {
            'first': {'hint_index': 0, 'word_index': 1},
            'second': {'hint_index': 3, 'word_index': 3},
            'lower_pair': {'word_indices': (3, 4), 'hint_index': 3},
        },
    }
