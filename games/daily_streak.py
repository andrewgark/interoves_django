"""Streaks for the official daily sections.

The streak is deliberately derived from completion history.  A completion only
counts when it happened on the Moscow calendar date on which that edition was
published; solving an old edition later therefore cannot repair a gap.
"""

from datetime import timedelta

from django.utils import timezone

from games.daily_section import MOSCOW, DAILY_TIMING_GAME_IDS, schedule_for
from games.models import GameTaskGroup, PlayerCompletedGame


def streak_from_completion_dates(completed_dates, *, today):
    """Return the current streak, keeping an unfinished today in the streak."""
    dates = set(completed_dates)
    cursor = today if today in dates else today - timedelta(days=1)
    streak = 0
    while cursor in dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def daily_streaks_for_user(user, *, games, now=None):
    """Return ``{game_id: streak}`` for an authenticated user in one query.

    The link query supplies the publication date for each task group.  The
    completion query is one bounded-by-scope query for all daily games, not one
    query per previous edition.
    """
    games = list(games)
    result = {str(game.id): 0 for game in games if str(game.id) in DAILY_TIMING_GAME_IDS}
    if not getattr(user, 'is_authenticated', False) or not result:
        return result

    now = now or timezone.now()
    today = now.astimezone(MOSCOW).date()
    game_ids = list(result)
    games_by_id = {str(game.id): game for game in games}
    links = list(
        GameTaskGroup.objects.filter(game_id__in=game_ids)
        .only('game_id', 'task_group_id', 'number')
    )
    link_dates = {}
    for link in links:
        schedule = schedule_for(link.game_id)
        published_at = schedule.publish_at(games_by_id[str(link.game_id)], link.number)
        if published_at is not None:
            link_dates[(str(link.game_id), link.task_group_id)] = published_at.astimezone(MOSCOW).date()

    completed_dates = {game_id: set() for game_id in game_ids}
    completions = (
        PlayerCompletedGame.objects
        .filter(user=user, game_id__in=game_ids, result=PlayerCompletedGame.RESULT_SOLVED)
        .only('game_id', 'task_group_id', 'completed_at')
    )
    for completion in completions:
        game_id = str(completion.game_id)
        published_date = link_dates.get((game_id, completion.task_group_id))
        if published_date is None:
            continue
        completed_date = completion.completed_at.astimezone(MOSCOW).date()
        if completed_date == published_date and published_date <= today:
            completed_dates[game_id].add(published_date)

    for game_id, dates in completed_dates.items():
        result[game_id] = streak_from_completion_dates(dates, today=today)
    return result
