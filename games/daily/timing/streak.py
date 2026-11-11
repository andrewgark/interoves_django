"""Streaks for the official daily sections.

The streak is deliberately derived from completion history. A completion is
assigned to its edition's Moscow publication date when it happens within the
streak window; solving an older edition after that cannot repair a gap.

The window is 36 hours, or 84 hours for a completion made while the player had
Club access (a paid, gift or manual entitlement). The window is judged by the
access at the moment of the completion, so streaks earned as a member stay
when the membership ends, and nothing is granted retroactively.

For a current member, an edition whose 84-hour window is still open does not
break the current run: it can still be solved in time. Without Club access the
run is unchanged: an unfinished today is kept, an unfinished yesterday is not.
"""

from datetime import timedelta

from django.db.models import Prefetch
from django.utils import timezone

from games.daily.page.section import DAILY_TIMING_GAME_IDS, MOSCOW, schedule_for
from games.models import DailySolveTiming, GameTaskGroup, PlayerCompletedGame, Profile, TaskGroup

STREAK_COMPLETION_WINDOW = timedelta(hours=36)
CLUB_STREAK_COMPLETION_WINDOW = timedelta(hours=84)


def club_access_intervals(user):
    """``[(starts_at, ends_at or None)]`` of a user's Club access, revoked rows aside.

    Every paid period, gift and manual grant is a ClubEntitlement row (Nov 11:
    each of the 28 members with access had a row covering that moment).
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return []
    from games.models import ClubEntitlement, ClubSubscription

    intervals = list(
        ClubEntitlement.objects.filter(user_id=user.pk, revoked_at__isnull=True)
        .values_list('starts_at', 'ends_at')
    )
    # ClubSubscription.grants_access also trusts paid_until, which a Tribute
    # cancellation can extend without a paid entitlement row: cover that
    # period too, so the archive and the streak agree on who is a member.
    period = ClubSubscription.objects.filter(user_id=user.pk).values_list(
        'current_period_start', 'last_payment_at', 'paid_until',
    ).first()
    if period:
        start, paid_until = period[0] or period[1], period[2]
        if start and paid_until and start < paid_until:
            intervals.append((start, paid_until))
    return intervals


def _has_access_at(moment, intervals):
    return any(start <= moment and (end is None or moment < end) for start, end in intervals)


def streak_window_at(moment, intervals=()):
    return CLUB_STREAK_COMPLETION_WINDOW if _has_access_at(moment, intervals) else STREAK_COMPLETION_WINDOW


def _within_streak_window(completed_at, published_at, intervals=()):
    return published_at <= completed_at <= published_at + streak_window_at(completed_at, intervals)


def _still_open_dates(publication_times, completed_dates, *, now, today, intervals):
    """Publication dates a current Club member can still solve in time."""
    if not _has_access_at(now, intervals):
        return set()
    return {
        published_at.astimezone(MOSCOW).date()
        for published_at in publication_times
        if published_at <= now < published_at + CLUB_STREAK_COMPLETION_WINDOW
        and published_at.astimezone(MOSCOW).date() <= today
        and published_at.astimezone(MOSCOW).date() not in completed_dates
    }


def _recent_publication_times(game, now):
    """Publication times of the editions published within the Club window.

    The caller's links may be one page of rows; the still-open editions must
    come from the whole section so they do not break the run shown there.
    """
    schedule = schedule_for(game.id)
    if schedule is None:
        return []
    today = now.astimezone(MOSCOW).date()
    candidates = {
        schedule.number_for_date(game, today - timedelta(days=days_back))
        for days_back in range(CLUB_STREAK_COMPLETION_WINDOW.days + 2)
    } - {None}
    existing = set(
        GameTaskGroup.objects.filter(game=game, number__in=[str(n) for n in candidates])
        .values_list('number', flat=True)
    )
    times = []
    for number in candidates:
        published_at = schedule.publish_at(game, number) if str(number) in existing else None
        if published_at is not None:
            times.append(published_at)
    return times


def _active_run(completed_dates, *, today, open_dates=()):
    """Dates of the current run, newest first.

    An unfinished today is kept. ``open_dates`` (unfinished editions a Club
    member can still solve in time) neither count nor break the run, wherever
    they fall in it.
    """
    dates = set(completed_dates)
    open_dates = set(open_dates)
    cursor = today if today in dates else today - timedelta(days=1)
    run = []
    while cursor in dates or cursor in open_dates:
        if cursor in dates:
            run.append(cursor)
        cursor -= timedelta(days=1)
    return run


def streak_from_completion_dates(completed_dates, *, today, open_dates=()):
    """Return the current streak, keeping an unfinished today in the streak."""
    return len(_active_run(completed_dates, today=today, open_dates=open_dates))


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
    intervals = club_access_intervals(user)
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
        key = (game_id, completion.task_group_id)
        published_date = link_dates.get(key)
        published_at = link_publication_times.get(key)
        if published_date is None or published_at is None:
            continue
        if (
            published_date <= today
            and _within_streak_window(completion.completed_at, published_at, intervals)
        ):
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
        open_dates = _still_open_dates(
            [at for (gid, _tg), at in link_publication_times.items() if gid == game_id],
            dates, now=now, today=today, intervals=intervals,
        )
        result[game_id] = streak_from_completion_dates(dates, today=today, open_dates=open_dates)
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
    intervals = club_access_intervals(user)
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

    on_time_dates = {
        link_dates[group_id]
        for group_id, completed_at in completed_at_by_group.items()
        if group_id in link_dates
        and link_dates[group_id] <= today
        and _within_streak_window(
            completed_at, link_publication_times[group_id], intervals,
        )
    }
    open_dates = _still_open_dates(
        _recent_publication_times(game, now) if _has_access_at(now, intervals) else (),
        on_time_dates, now=now, today=today, intervals=intervals,
    )
    active_dates = set(_active_run(on_time_dates, today=today, open_dates=open_dates))

    statuses = {}
    for group_id, completed_at in completed_at_by_group.items():
        published_date = link_dates.get(group_id)
        if published_date is None:
            continue
        if _within_streak_window(
            completed_at, link_publication_times[group_id], intervals,
        ):
            statuses[group_id] = 'streak' if published_date in active_dates else 'same_day'
        else:
            statuses[group_id] = 'late'
    return statuses
