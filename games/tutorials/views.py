from games.ladder_daily import LADDER_GAME_ID
from games.tutorials.ladder import LADDER_TUTORIAL_SEGMENT, ensure_ladder_tutorial_task


def ladder_tutorial(request):
    """The tutorial Ladder on the regular play page, restarted on every visit."""
    from games.views.new_ui import new_task_group_page

    return new_task_group_page(
        request, LADDER_GAME_ID, LADDER_TUTORIAL_SEGMENT,
        ladder_tutorial_task=ensure_ladder_tutorial_task(),
    )
