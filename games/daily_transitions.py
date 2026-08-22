"""Server-derived clock boundary for scheduled daily/weekly section content."""

from django.utils import timezone

from games.daily_section import publish_at_for, schedule_for


def next_daily_content_transition(game, numbers, *, now=None):
    """Earliest future publish time among content rows that already exist."""
    if schedule_for(getattr(game, 'id', None)) is None:
        return None
    now = now or timezone.now()
    candidates = []
    for number in numbers:
        transition = publish_at_for(game, number)
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
