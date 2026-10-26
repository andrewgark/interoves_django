from games.ladder_daily import LADDER_GAME_ID
from games.ladder.tutorial.ladder import (
    LADDER_TUTORIAL_SEGMENT,
    ensure_ladder_tutorial_task,
    ladder_tutorial_return,
)


def ladder_tutorial(request):
    """The tutorial Ladder on the regular play page, restarted on every visit.

    ``?next=/ladder/<n>/`` is the Ladder the player left for the tutorial;
    the tutorial leads back there.
    """
    from games.site.ui.play.page import new_task_group_page

    return new_task_group_page(
        request, LADDER_GAME_ID, LADDER_TUTORIAL_SEGMENT,
        ladder_tutorial_task=ensure_ladder_tutorial_task(),
        ladder_tutorial_return_to=ladder_tutorial_return(request.GET.get('next')),
    )
