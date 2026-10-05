from django.shortcuts import render

from games.tutorials.ladder import LADDER_TUTORIAL


def ladder_tutorial(request):
    """Render only the static tutorial shell; no gameplay actor is resolved."""
    return render(request, 'new/ladder_tutorial.html', {
        'tutorial': LADDER_TUTORIAL.payload(),
        'tutorial_task': {
            'id': 'ladder-tutorial-demo', 'attempt_revision': '',
            'tags': {}, 'text': '',
        },
        'tutorial_raddle': {'ui': LADDER_TUTORIAL.ui_context()},
    })
