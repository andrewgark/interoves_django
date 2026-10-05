from django.shortcuts import render

from games.tutorials.ladder import LADDER_TUTORIAL


def ladder_tutorial(request):
    """Render only the static tutorial shell; no gameplay actor is resolved."""
    return render(request, 'new/ladder_tutorial.html', {
        'tutorial': LADDER_TUTORIAL.payload(),
        'tutorial_task': {
            'id': 'ladder-tutorial-demo', 'number': 7,
            'attempt_revision': '', 'task_type': 'raddle',
            'tags': {}, 'text': 'Лесенка #7',
        },
        'tutorial_ai': {
            'is_solved': False, 'get_result_points': 0,
            'get_n_attempts': 0, 'attempts': [],
        },
        'tutorial_task_ui': {
            'show_attempts': False, 'show_answer': False,
        },
        'tutorial_raddle': {'ui': LADDER_TUTORIAL.ui_context()},
    })
