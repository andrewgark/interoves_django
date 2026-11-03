"""Read model for a player's public daily-game statistics.

Only personal play is attributed to a person: in team mode the result
belongs to the team and is shown on the team page instead. Every number
here comes from rebuildable read models (completions, timings, result
projections and published monthly snapshots), never from raw attempts.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import Count, Q
from django.utils import timezone

from games.daily.page.section import DAILY_TIMING_GAME_IDS, MOSCOW
from games.models import (
    DailyAggregateSnapshot,
    DailyAggregateSnapshotRow,
    DailyResultProjection,
    DailySolveTiming,
    Game,
    HiddenAnonKey,
    PlayerCompletedGame,
)

ACTIVITY_WEEKS = 16
MONTH_NAMES_PREPOSITIONAL = (
    '', 'январе', 'феврале', 'марте', 'апреле', 'мае', 'июне',
    'июле', 'августе', 'сентябре', 'октябре', 'ноябре', 'декабре',
)


@dataclass
class GameStats:
    game_id: str
    name: str
    played: int = 0
    solved: int = 0
    median_seconds: float | None = None
    streak: int = 0
    month_place: int | None = None
    month_total: int | None = None
    month_label: str = ''

    @property
    def median_display(self):
        return format_duration(self.median_seconds)


@dataclass
class PlayerStats:
    games: list = field(default_factory=list)
    total_played: int = 0
    active_days: int = 0
    current_day_streak: int = 0
    best_day_streak: int = 0
    activity: list = field(default_factory=list)  # weeks × 7 cells
    activity_first_day: date | None = None

    @property
    def has_any(self):
        return self.total_played > 0


@dataclass
class HeadToHead:
    common: int = 0
    wins: int = 0
    losses: int = 0
    ties: int = 0


def format_duration(seconds):
    if seconds is None:
        return ''
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return '{}:{:02d}:{:02d}'.format(hours, minutes, secs)
    return '{}:{:02d}'.format(minutes, secs)


@dataclass(frozen=True)
class _Actor:
    """Filters selecting one results actor across the daily read models."""
    kind: str  # DailyResultProjection.ACTOR_USER or ACTOR_TEAM
    obj: object

    @property
    def own(self):
        if self.kind == DailyResultProjection.ACTOR_TEAM:
            return {'team': self.obj}
        return {'user': self.obj, 'team__isnull': True}

    @property
    def snapshot_row(self):
        if self.kind == DailyResultProjection.ACTOR_TEAM:
            return {'actor_type': self.kind, 'team': self.obj}
        return {'actor_type': self.kind, 'user': self.obj}


def day_streaks(days, *, today):
    """Return ``(current, best)`` runs of consecutive days.

    The current run survives an unfinished today, like the daily streak.
    """
    days = set(days)
    cursor = today if today in days else today - timedelta(days=1)
    current = 0
    while cursor in days:
        current += 1
        cursor -= timedelta(days=1)
    best = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return current, best


def _activity_grid(day_counts, *, today, weeks=ACTIVITY_WEEKS):
    """Monday-first weeks ending with the current week; levels 0..3."""
    first = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    grid = []
    for week in range(weeks):
        column = []
        for weekday in range(7):
            day = first + timedelta(days=week * 7 + weekday)
            count = day_counts.get(day, 0)
            column.append({
                'date': day,
                'count': count,
                'level': 0 if count == 0 else (1 if count == 1 else (2 if count <= 3 else 3)),
                'future': day > today,
            })
        grid.append(column)
    return grid, first


def _median_times(actor, game_ids):
    rows = DailySolveTiming.objects.filter(
        replay_slot__isnull=True, status=DailySolveTiming.STATUS_COMPLETED,
        game_id__in=game_ids, **actor.own,
    ).values_list('game_id', 'task_group_id', 'frozen_ms', 'accumulated_ms')
    # A team may keep several timing rows per release (one per timing key);
    # the release took as long as its fastest completed run.
    per_release = {}
    for game_id, group_id, frozen_ms, accumulated_ms in rows:
        value = frozen_ms if frozen_ms is not None else accumulated_ms
        if value and value > 0:
            key = (str(game_id), group_id)
            per_release[key] = min(value, per_release.get(key, value))
    by_game = defaultdict(list)
    for (game_id, _group_id), value in per_release.items():
        by_game[game_id].append(value / 1000)
    return {game_id: statistics.median(values) for game_id, values in by_game.items()}


def _visible_rows(version):
    return DailyAggregateSnapshotRow.objects.filter(version=version).filter(
        Q(team__isnull=True) | Q(team__is_hidden=False),
    ).filter(
        Q(user__isnull=True) | Q(user__profile__is_hidden=False),
    ).filter(
        Q(anon_key__isnull=True) | ~Q(anon_key__in=HiddenAnonKey.objects.values('anon_key')),
    )


def _month_places(actor, game_ids, *, today):
    """Place in the current month's published table that includes the actor.

    Ranking matches the table (RANK over score), so equal scores share a
    place. Only the current month is considered: last month's place is stale
    on a profile.
    """
    month = date(today.year, today.month, 1)
    snapshots = DailyAggregateSnapshot.objects.filter(
        game_id__in=game_ids, month=month, published_version__isnull=False,
    ).values_list('game_id', 'published_version_id')
    out = {}
    for game_id, version_id in snapshots:
        mine = DailyAggregateSnapshotRow.objects.filter(
            version_id=version_id, **actor.snapshot_row,
        ).values_list('score', flat=True).first()
        if mine is None:
            continue
        rows = _visible_rows(version_id)
        out[str(game_id)] = (
            rows.filter(score__gt=mine).count() + 1,
            rows.count(),
            'в {}'.format(MONTH_NAMES_PREPOSITIONAL[month.month]),
        )
    return out


def player_stats(user, *, now=None):
    """Personal play only: in team mode the result belongs to the team."""
    return _actor_stats(_Actor(DailyResultProjection.ACTOR_USER, user), now=now)


def team_stats(team, *, now=None):
    return _actor_stats(_Actor(DailyResultProjection.ACTOR_TEAM, team), now=now)


def _actor_stats(actor, *, now=None):
    from games.daily.activity import active_days
    from games.daily.timing.streak import daily_streaks_for_actor

    now = now or timezone.now()
    today = now.astimezone(MOSCOW).date()
    is_user = actor.kind == DailyResultProjection.ACTOR_USER
    completions = PlayerCompletedGame.objects.filter(
        game_id__in=DAILY_TIMING_GAME_IDS, **actor.own,
    )
    counts = {
        str(row['game_id']): row for row in completions.values('game_id').annotate(
            played=Count('id'),
            solved=Count('id', filter=~Q(result=PlayerCompletedGame.RESULT_FAILED)),
        )
    }
    stats = PlayerStats()
    if not counts:
        stats.activity, stats.activity_first_day = _activity_grid({}, today=today)
        return stats

    games = {str(g.pk): g for g in Game.objects.filter(pk__in=list(counts))}
    medians = _median_times(actor, list(counts))
    # Publication-date streaks exist for personal actors only.
    streaks = daily_streaks_for_actor(
        games=list(games.values()), user=actor.obj, now=now,
    ) if is_user else {}
    places = _month_places(actor, list(counts), today=today)
    for game_id, row in counts.items():
        game = games.get(game_id)
        if game is None:
            continue
        place = places.get(game_id)
        stats.games.append(GameStats(
            game_id=game_id,
            name=game.get_no_html_name() or game.name,
            played=row['played'],
            solved=row['solved'],
            median_seconds=medians.get(game_id),
            streak=streaks.get(game_id, 0),
            month_place=place[0] if place else None,
            month_total=place[1] if place else None,
            month_label=place[2] if place else '',
        ))
    stats.games.sort(key=lambda item: (-item.played, item.name))
    stats.total_played = sum(item.played for item in stats.games)

    day_counts = defaultdict(int)
    for moment in completions.values_list('completed_at', flat=True):
        day_counts[moment.astimezone(MOSCOW).date()] += 1
    stats.active_days = (active_days(user=actor.obj) if is_user else 0) or len(day_counts)
    stats.current_day_streak, stats.best_day_streak = day_streaks(day_counts, today=today)
    stats.activity, stats.activity_first_day = _activity_grid(day_counts, today=today)
    return stats


def _timing_ms(user):
    rows = DailySolveTiming.objects.filter(
        user=user, team__isnull=True, replay_slot__isnull=True,
        status=DailySolveTiming.STATUS_COMPLETED,
    ).values_list('game_id', 'task_group_id', 'frozen_ms', 'accumulated_ms')
    return {
        (str(game_id), group_id): (frozen if frozen is not None else accumulated)
        for game_id, group_id, frozen, accumulated in rows
    }


def head_to_head(viewer, other):
    """Compare two players on the daily releases both played personally.

    Higher score wins; equal scores are decided by the solve timer when both
    have one, otherwise the release counts as a tie.
    """
    def scores(user):
        return {
            (str(game_id), group_id): score
            # Pre-publication plays (authors, testers) are not part of any
            # public table, so they are not part of the comparison either.
            for game_id, group_id, score in DailyResultProjection.objects.filter(
                actor_type=DailyResultProjection.ACTOR_USER, user=user,
                game_id__in=DAILY_TIMING_GAME_IDS, is_prepublication=False,
            ).values_list('game_id', 'task_group_id', 'score')
        }

    mine, theirs = scores(viewer), scores(other)
    common = mine.keys() & theirs.keys()
    result = HeadToHead(common=len(common))
    if not common:
        return result
    my_time, their_time = _timing_ms(viewer), _timing_ms(other)
    for key in common:
        if mine[key] != theirs[key]:
            won = mine[key] > theirs[key]
        else:
            a, b = my_time.get(key), their_time.get(key)
            if not a or not b or a == b:
                result.ties += 1
                continue
            won = a < b
        if won:
            result.wins += 1
        else:
            result.losses += 1
    return result
