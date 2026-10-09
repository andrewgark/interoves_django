"""Streaks for the official daily sections.

The streak is deliberately derived from completion history.  A completion only
counts when it happened on the Moscow calendar date on which that edition was
published; solving an old edition later therefore cannot repair a gap.
"""

from datetime import timedelta

from django.db.models import Prefetch
from django.utils import timezone

from games.daily.section import MOSCOW, DAILY_TIMING_GAME_IDS, schedule_for
from games.models import DailySolveTiming, GameTaskGroup, PlayerCompletedGame, Profile, TaskGroup


def streak_from_completion_dates(completed_dates, *, today):
    """Return the current streak, keeping an unfinished today in the streak."""
    dates = set(completed_dates)
    cursor = today if today in dates else today - timedelta(days=1)
    streak = 0
    while cursor in dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def daily_streaks_for_actor(*, games, user=None, anon_key=None, now=None):
    """Return ``{game_id: streak}`` for one personal actor in one query.

    The link query supplies the publication date for each task group.  The
    completion query is one bounded-by-scope query for all daily games, not one
    query per previous edition.
    """
    games = list(games)
    result = {str(game.id): 0 for game in games if str(game.id) in DAILY_TIMING_GAME_IDS}
    if not result or (
        user is None and not anon_key
    ) or (
        user is not None and not getattr(user, 'is_authenticated', False)
    ):
        return result

    now = now or timezone.now()
    today = now.astimezone(MOSCOW).date()
    game_ids = list(result)
    games_by_id = {str(game.id): game for game in games}
    links_qs = GameTaskGroup.objects.filter(game_id__in=game_ids).only(
        'game_id', 'task_group_id', 'number', 'task_group',
    )
    if user is not None:
        links_qs = links_qs.prefetch_related(Prefetch(
            'task_group__authors',
            queryset=Profile.objects.filter(user_id=user.pk),
        ))
    links = list(links_qs)
    link_dates = {}
    link_publication_times = {}
    for link in links:
        schedule = schedule_for(link.game_id)
        if schedule is None:
            # Some public daily sections use daily timing without having a
            # calendar-backed publication schedule.  They cannot contribute
            # to a publication-date streak, but must not break the hub.
            continue
        published_at = schedule.publish_at(games_by_id[str(link.game_id)], link.number)
        if published_at is not None:
            key = (str(link.game_id), link.task_group_id)
            link_dates[key] = published_at.astimezone(MOSCOW).date()
            link_publication_times[key] = published_at

    completed_dates = {game_id: set() for game_id in game_ids}
    completions = PlayerCompletedGame.objects.filter(
        game_id__in=game_ids,
        result=PlayerCompletedGame.RESULT_SOLVED,
        **(
            {'user': user, 'team__isnull': True, 'anon_key__isnull': True}
            if user is not None
            else {'anon_key': str(anon_key), 'team__isnull': True, 'user__isnull': True}
        ),
    ).only('game_id', 'task_group_id', 'completed_at')
    for completion in completions:
        game_id = str(completion.game_id)
        published_date = link_dates.get((game_id, completion.task_group_id))
        if published_date is None:
            continue
        completed_date = completion.completed_at.astimezone(MOSCOW).date()
        if completed_date == published_date and published_date <= today:
            completed_dates[game_id].add(published_date)

    # Daily releases are calendar-backed rather than materialized publication
    # events.  An author therefore receives the equivalent of a completion
    # without creating an Attempt/PlayerCompletedGame row (which could leak
    # into timing, analytics, or result projections).
    if user is not None:
        for link in links:
            key = (str(link.game_id), link.task_group_id)
            published_date = link_dates.get(key)
            published_at = link_publication_times.get(key)
            if (
                published_date is None
                or published_at is None
                or published_at > now
                or published_date > today
            ):
                continue
            if link.task_group.authors.all():
                completed_dates[str(link.game_id)].add(published_date)

    for game_id, dates in completed_dates.items():
        result[game_id] = streak_from_completion_dates(dates, today=today)
    return result


def daily_streaks_for_user(user, *, games, now=None):
    """Backward-compatible wrapper for authenticated-user callers."""
    return daily_streaks_for_actor(user=user, games=games, now=now)


def daily_completion_statuses_for_actor(*, game, links, user=None, anon_key=None, now=None):
    """Return per-task-group solve timing, using the same dates as Streak.

    Values are ``same_day``, ``late`` or ``streak``.  The latter is limited to
    the currently active consecutive run, so an old on-time solve stays a
    regular (but still positive) flame.
    """
    if (user is None or not getattr(user, 'is_authenticated', False)) and not anon_key:
        return {}
    now = now or timezone.now()
    today = now.astimezone(MOSCOW).date()
    links = list(links)
    link_dates = {}
    link_publication_times = {}
    task_group_ids = []
    for link in links:
        game_link = link[1] if isinstance(link, tuple) else link
        number = link[0] if isinstance(link, tuple) else getattr(link, 'number', None)
        schedule = schedule_for(game.id)
        published_at = schedule.publish_at(game, number) if schedule and number is not None else None
        if published_at is None:
            continue
        task_group_id = getattr(game_link, 'task_group_id', None)
        if task_group_id is None:
            continue
        link_dates[task_group_id] = published_at.astimezone(MOSCOW).date()
        link_publication_times[task_group_id] = published_at
        task_group_ids.append(task_group_id)
    if not task_group_ids:
        return {}

    actor = (
        {'user': user, 'team__isnull': True, 'anon_key__isnull': True}
        if user is not None and getattr(user, 'is_authenticated', False)
        else {'anon_key': str(anon_key), 'team__isnull': True, 'user__isnull': True}
    )
    completed_at_by_group = {}
    for row in PlayerCompletedGame.objects.filter(
        game=game, task_group_id__in=task_group_ids,
        result=PlayerCompletedGame.RESULT_SOLVED, **actor,
    ).only('task_group_id', 'completed_at').order_by('completed_at'):
        completed_at_by_group.setdefault(row.task_group_id, row.completed_at)

    # Older completions can predate the canonical timing row.  Prefer the
    # same source as daily_streaks_for_actor when both records exist.
    for row in DailySolveTiming.objects.filter(
        game=game, task_group_id__in=task_group_ids,
        status=DailySolveTiming.STATUS_COMPLETED, replay_slot__isnull=True, **actor,
    ).only('task_group_id', 'completed_at'):
        if row.completed_at:
            completed_at_by_group.setdefault(row.task_group_id, row.completed_at)

    # Match the existing Streak rule: an author gets personal credit for a
    # published release without a completion row.
    if user is not None and getattr(user, 'is_authenticated', False):
        authored_groups = set(
            TaskGroup.objects.filter(
                pk__in=task_group_ids, authors__user_id=user.pk,
            ).values_list('pk', flat=True)
        )
        for task_group_id in authored_groups:
            published_at = link_publication_times.get(task_group_id)
            if published_at is not None and published_at <= now:
                completed_at_by_group.setdefault(task_group_id, published_at)

    same_day_dates = {
        link_dates[group_id]
        for group_id, completed_at in completed_at_by_group.items()
        if group_id in link_dates
        and completed_at.astimezone(MOSCOW).date() == link_dates[group_id]
        and link_dates[group_id] <= today
    }
    cursor = today if today in same_day_dates else today - timedelta(days=1)
    active_dates = set()
    while cursor in same_day_dates:
        active_dates.add(cursor)
        cursor -= timedelta(days=1)

    statuses = {}
    for group_id, completed_at in completed_at_by_group.items():
        published_date = link_dates.get(group_id)
        if published_date is None:
            continue
        completed_date = completed_at.astimezone(MOSCOW).date()
        if completed_date == published_date:
            statuses[group_id] = 'streak' if published_date in active_dates else 'same_day'
        else:
            statuses[group_id] = 'late'
    return statuses
