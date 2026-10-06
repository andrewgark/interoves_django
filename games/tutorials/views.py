from django.shortcuts import render

from games.tutorials.ladder import LADDER_TUTORIAL


def ladder_tutorial(request):
    """Render an isolated raddle demo; never resolve a production task."""
    task = LADDER_TUTORIAL.task()
    return render(request, 'new/ladder_tutorial.html', {
        'tutorial': LADDER_TUTORIAL.payload(),
        'tutorial_task': task,
        'tutorial_ai': {'is_solved': False, 'get_result_points': 0,
                        'get_n_attempts': 0, 'attempts': []},
        'tutorial_task_ui': {'show_attempts': False, 'show_answer': False},
        'tutorial_raddle': {'ui': LADDER_TUTORIAL.ui_context()},
    })
