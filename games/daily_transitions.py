"""Server-derived clock boundary for scheduled daily/weekly section content."""

from django.utils import timezone

from games.alphabetty_daily import ALPHABETTY_GAME_ID, alphabetty_publish_at
from games.ladder_daily import LADDER_GAME_ID, ladder_publish_at
from games.week_task_weekly import WEEK_TASK_GAME_ID, week_task_publish_at


PUBLISH_AT_BY_GAME_ID = {
    LADDER_GAME_ID: ladder_publish_at,
    ALPHABETTY_GAME_ID: alphabetty_publish_at,
    WEEK_TASK_GAME_ID: week_task_publish_at,
}


def next_daily_content_transition(game, numbers, *, now=None):
    """Earliest future publish time among content rows that already exist."""
    publish_at = PUBLISH_AT_BY_GAME_ID.get(getattr(game, 'id', None))
    if publish_at is None:
        return None
    now = now or timezone.now()
    candidates = []
    for number in numbers:
        transition = publish_at(game, number)
        if transition is not None and transition > now:
            candidates.append(transition)
    return min(candidates) if candidates else None


def next_daily_content_transition_for_game(game, *, now=None):
    from games.models import GameTaskGroup

    numbers = GameTaskGroup.objects.filter(game=game).values_list('number', flat=True)
    return next_daily_content_transition(game, numbers, now=now)


def next_daily_content_transition_for_games(games, *, now=None):
    now = now or timezone.now()
    transitions = [
        next_daily_content_transition_for_game(game, now=now)
        for game in games
        if game is not None
    ]
    transitions = [transition for transition in transitions if transition is not None]
    return min(transitions) if transitions else None
