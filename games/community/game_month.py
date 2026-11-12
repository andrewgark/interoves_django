"""One daily game in one month for a player or team page.

The month view feeds the per-game chart: every published release of the
month with the actor's result, the public median and, on another player's
page, the viewer's own result. Medians use the same cohort as the release
statistics page (public solvers, timer seconds), so the numbers agree.
"""

import json
import logging
import statistics as stats_lib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from django.core.cache import caches
from django.db import DatabaseError
from django.db.models import Q
from django.db.models.fields.json import KeyTextTransform
from django.utils import timezone

from games.daily.page.section import MOSCOW
from games.daily.registry import daily_game_counts_attempts, get_daily_game
from games.models import (
    ChainTaskState,
    DailyAggregateSnapshot,
    DailyResultProjection,
    DailySolveTiming,
    DailyTaskResultProjection,
    Game,
    HiddenAnonKey,
    PlayerCompletedGame,
    Profile,
    TaskGroup,
)

logger = logging.getLogger(__name__)

MONTH_NAMES = (
    '', 'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
)
BASELINE_CACHE_VERSION = 2
# A release keeps collecting players for a few days; after that its median
# barely moves, so it is cached for long in the shared statistics cache
# (the default cache is per-process LocMem in production).
BASELINE_FRESH_FOR = timedelta(days=3)
BASELINE_TTL_FRESH = 10 * 60
BASELINE_TTL_SETTLED = 30 * 24 * 60 * 60


@dataclass(frozen=True)
class Actor:
    """The player or team whose page is shown."""
    kind: str  # DailyResultProjection.ACTOR_USER or ACTOR_TEAM
    obj: object

    @property
    def key(self):
        return (self.kind, str(self.obj.pk))

    @property
    def own(self):
        if self.kind == DailyResultProjection.ACTOR_TEAM:
            return {'team': self.obj}
        return {'user': self.obj, 'team__isnull': True}

    @property
    def projection(self):
        return {'actor_type': self.kind, 'actor_key': str(self.obj.pk)}

    @property
    def is_team(self):
        return self.kind == DailyResultProjection.ACTOR_TEAM


def user_actor(user):
    return Actor(DailyResultProjection.ACTOR_USER, user)


def team_actor(team):
    return Actor(DailyResultProjection.ACTOR_TEAM, team)


def month_games():
    """Daily games that have monthly tables, in registry order."""
    from games.daily.registry import DAILY_GAME_REGISTRY
    return [d.game_id for d in DAILY_GAME_REGISTRY.all() if d.capabilities.aggregate_results]


def _norm_key(kind, value):
    return (kind, str(value))


def _row_key(row):
    """Results actor of an ORM row or values() dict (team wins over user)."""
    get = row.get if isinstance(row, dict) else (lambda name: getattr(row, name, None))
    if get('team_id'):
        return _norm_key('team', get('team_id'))
    if get('user_id'):
        return _norm_key('user', get('user_id'))
    if get('anon_key'):
        return _norm_key('anon', get('anon_key'))
    return None


def _actor_row_q(actor):
    """Filter for Attempt / ChainTaskState rows of one actor."""
    if actor.is_team:
        return Q(team=actor.obj)
    return Q(user=actor.obj, team__isnull=True)


def _round(value):
    return round(value, 1) if value is not None else None


def _median(values):
    values = [v for v in values if v is not None]
    return float(stats_lib.median(values)) if values else None


# --- hints -----------------------------------------------------------------

def _censorly_hint_counts(game, group_ids, actor, out):
    """``hints_taken`` read by the database.

    Цензурка states carry the whole guess history: loading them, or scanning
    ``#hint:`` attempts, took ~20 s for a month.
    """
    qs = ChainTaskState.objects.filter(
        game=game, task__task_group_id__in=group_ids,
        game_mode='general', replay_slot__isnull=True,
    ).annotate(hints=KeyTextTransform('hints_taken', 'state')).filter(hints__isnull=False)
    if actor is not None:
        qs = qs.filter(_actor_row_q(actor))
    for row in qs.values('task__task_group_id', 'team_id', 'user_id', 'anon_key', 'hints'):
        key = _row_key(row)
        try:
            count = max(0, int(row['hints']))
        except (TypeError, ValueError):
            continue
        if key:
            out[(row['task__task_group_id'], key)] += count
    return out


def _hint_counts(game, task_type, group_ids, actor=None):
    """``{(group_id, actor_key): hints}``; only actors with a stored trace.

    Every game keeps its hints differently: Лесенка in the result
    projection, the others in the chain state (Цензурка's is read by key).
    """
    out = defaultdict(int)
    if not group_ids:
        return out
    if task_type == 'raddle':
        qs = DailyTaskResultProjection.objects.filter(game=game, task_group_id__in=group_ids)
        if actor is not None:
            qs = qs.filter(**actor.projection)
        for row in qs.values('task_group_id', 'actor_type', 'actor_key', 'hint_numbers'):
            out[(row['task_group_id'], (row['actor_type'], row['actor_key']))] += len(row['hint_numbers'] or [])
        return out
    if task_type == 'censorly':
        try:
            return _censorly_hint_counts(game, group_ids, actor, out)
        except DatabaseError:
            # A state that is not valid JSON makes the database refuse the
            # key lookup for the whole query; parse in Python instead.
            logger.warning('profile_censorly_hint_json_failed game=%s', game.pk, exc_info=True)
            out.clear()
    if task_type in ('alphabetty', 'word_salad', 'censorly'):
        if task_type == 'censorly':
            from games.puzzles.censorly.play import hint_count

            def counter(state):
                return hint_count(json.loads(state) if state else {})
        elif task_type == 'alphabetty':
            from games.puzzles.alphabetty.play import hint_count, load_state

            def counter(state):
                return hint_count(load_state(state))
        else:
            from games.puzzles.salad.rules import load_state

            def counter(state):
                return sum(int(v or 0) for v in (load_state(state).get('hint_counts') or {}).values())
        qs = ChainTaskState.objects.filter(
            game=game, task__task_group_id__in=group_ids,
            game_mode='general', replay_slot__isnull=True,
        )
        if actor is not None:
            qs = qs.filter(_actor_row_q(actor))
        for row in qs.values('task__task_group_id', 'team_id', 'user_id', 'anon_key', 'state'):
            key = _row_key(row)
            if key:
                try:
                    out[(row['task__task_group_id'], key)] += counter(row['state'])
                except Exception:
                    continue
        return out
    return out


# --- per-release baselines (cached) ---------------------------------------

def _compute_baselines(game, task_type, columns):
    """Medians for every release in ``columns`` with a few bulk queries.

    The cohort is the release statistics' one: public solvers (no authors,
    no pre-publication players, no hidden actors), timed by the solve
    timer. Running the statistics helpers per release cost ~7 queries a
    release, which made the first view of a month take tens of seconds.
    """
    from games.daily.timing import canonical_elapsed_seconds, timing_row_preference
    from games.results.snapshots.aggregate_snapshots import _authored_groups

    group_ids = [col.link.task_group_id for col in columns]
    by_user, by_team = _authored_groups(group_ids)

    def written(group_id, key):
        kind, value = key
        if kind == 'user':
            return group_id in by_user.get(int(value), ())
        if kind == 'team':
            return group_id in by_team.get(value, ())
        return False

    # Public table rows: the score cohort, and the pool solvers come from.
    eligible = defaultdict(dict)
    for group_id, kind, key, score, attempts in DailyResultProjection.objects.filter(
        game=game, task_group_id__in=group_ids, is_prepublication=False,
    ).exclude(user__profile__is_hidden=True).exclude(team__is_hidden=True).exclude(
        anon_key__in=HiddenAnonKey.objects.values('anon_key'),
    ).values_list('task_group_id', 'actor_type', 'actor_key', 'score', 'attempts_count'):
        if not written(group_id, (kind, key)):
            eligible[group_id][(kind, key)] = (float(score), attempts)

    solved = defaultdict(set)
    for row in PlayerCompletedGame.objects.filter(
        game=game, task_group_id__in=group_ids, result=PlayerCompletedGame.RESULT_SOLVED,
    ).values('task_group_id', 'team_id', 'user_id', 'anon_key'):
        key = _row_key(row)
        if key and key in eligible[row['task_group_id']]:
            solved[row['task_group_id']].add(key)

    best = {}
    for row in DailySolveTiming.objects.filter(
        game=game, task_group_id__in=group_ids, replay_slot__isnull=True,
        status=DailySolveTiming.STATUS_COMPLETED, timing_version__gte=1,
    ).only(
        'task_group_id', 'team_id', 'user_id', 'anon_key', 'status', 'frozen_ms',
        'accumulated_ms', 'timing_version', 'completed_at', 'updated_at', 'pk',
    ):
        key = _row_key(row)
        if not key or key not in solved[row.task_group_id]:
            continue
        slot = (row.task_group_id, key)
        if slot not in best or timing_row_preference(row) > timing_row_preference(best[slot]):
            best[slot] = row
    times = defaultdict(list)
    for (group_id, _key), row in best.items():
        seconds = canonical_elapsed_seconds(game=game, timing_row=row)
        if seconds is not None:
            times[group_id].append(seconds)

    hint_map = _hint_counts(game, task_type, group_ids)
    counts_attempts = daily_game_counts_attempts(game.pk)
    out = {}
    for col in columns:
        group_id = col.link.task_group_id
        max_score = float(col.max_score) if col.max_score else None
        rows = eligible[group_id]
        solvers = solved[group_id]
        out[group_id] = {
            'time': _median(times[group_id]),
            'att': _median([rows[key][1] for key in solvers]) if counts_attempts else None,
            'hints': _median([hint_map.get((group_id, key), 0) for key in solvers]),
            'score': _round(_median([score * 100 / max_score for score, _a in rows.values()])) if max_score else None,
            'n': len(solvers),
        }
    return out


def _baseline_key(game, group_id):
    return 'profile-release-baseline:v{}:{}:{}'.format(BASELINE_CACHE_VERSION, game.pk, group_id)


def release_baselines(game, task_type, columns, *, now=None):
    """``{group_id: baseline}``; cached per release, missing ones in bulk."""
    now = now or timezone.now()
    cache = caches['daily_statistics']
    keys = {col.link.task_group_id: _baseline_key(game, col.link.task_group_id) for col in columns}
    try:
        cached = cache.get_many(list(keys.values()))
    except Exception:
        cached = {}
    out = {group_id: cached[key] for group_id, key in keys.items() if key in cached}
    missing = [col for col in columns if col.link.task_group_id not in out]
    if not missing:
        return out
    fresh = _compute_baselines(game, task_type, missing)
    settled, recent = {}, {}
    for col in missing:
        group_id = col.link.task_group_id
        out[group_id] = fresh[group_id]
        old = col.published_at is not None and now - col.published_at > BASELINE_FRESH_FOR
        (settled if old else recent)[keys[group_id]] = fresh[group_id]
    try:
        if settled:
            cache.set_many(settled, BASELINE_TTL_SETTLED)
        if recent:
            cache.set_many(recent, BASELINE_TTL_FRESH)
    except Exception:
        pass
    return out


# --- one actor's releases ---------------------------------------------------

def _timer_ms(row):
    value = row.frozen_ms if row.frozen_ms is not None else row.accumulated_ms
    return int(value) if value and value > 0 else None


def actor_releases(game, task_type, actor, group_ids):
    """``{group_id: {...}}`` for releases the actor started."""
    from games.daily.timing import canonical_elapsed_seconds, timing_row_preference

    out = {}
    for group_id, score, attempts, pre in DailyResultProjection.objects.filter(
        game=game, task_group_id__in=group_ids, **actor.projection,
    ).values_list('task_group_id', 'score', 'attempts_count', 'is_prepublication'):
        out[group_id] = {'score': float(score), 'att': attempts, 'pre': pre}
    results = dict(PlayerCompletedGame.objects.filter(
        game=game, task_group_id__in=group_ids, **actor.own,
    ).values_list('task_group_id', 'result'))
    best = {}
    for row in DailySolveTiming.objects.filter(
        game=game, task_group_id__in=group_ids, replay_slot__isnull=True,
        status=DailySolveTiming.STATUS_COMPLETED, **actor.own,
    ):
        if not row.timing_version:
            continue
        if row.task_group_id not in best or timing_row_preference(row) > timing_row_preference(best[row.task_group_id]):
            best[row.task_group_id] = row
    hints = _hint_counts(game, task_type, group_ids, actor)
    for group_id in set(out) | set(results):
        item = out.setdefault(group_id, {'score': None, 'att': None, 'pre': False})
        result = results.get(group_id)
        item['solved'] = result is not None and result != PlayerCompletedGame.RESULT_FAILED
        timing = best.get(group_id)
        item['time'] = (
            canonical_elapsed_seconds(game=game, timing_row=timing)
            if timing is not None and item['solved'] else None
        )
        item['time_ms'] = _timer_ms(timing) if timing is not None else None
        item['hints'] = hints.get((group_id, actor.key), 0)
        if not daily_game_counts_attempts(game.pk):
            item['att'] = None
    return out


def authored_group_ids(actor, group_ids=None):
    """Task groups among ``group_ids`` written by the player or a team member."""
    through = TaskGroup.authors.through.objects.all()
    if group_ids is not None:
        through = through.filter(taskgroup_id__in=group_ids)
    if actor.is_team:
        members = Profile.objects.filter(
            Q(team_memberships__team=actor.obj) | Q(team_on=actor.obj),
        ).values('user_id')
        through = through.filter(profile_id__in=members)
    else:
        through = through.filter(profile_id=actor.obj.pk)
    return set(through.values_list('taskgroup_id', flat=True))


def authored_release_count(actor, game):
    from games.daily.page.section import current_number_for
    from games.results.standings.aggregate_leaderboard import _numbered_links

    authored = authored_group_ids(actor)
    if not authored:
        return 0
    if current_number_for(game) is None:
        return 0
    return _numbered_links(game).filter(task_group_id__in=authored).count()


# --- head to head ---------------------------------------------------------

@dataclass
class Duel:
    common: int = 0
    wins: int = 0
    ties: int = 0
    losses: int = 0

    def add(self, mine, theirs):
        """``mine`` / ``theirs``: (score, timer ms) of one shared release."""
        self.common += 1
        if mine[0] != theirs[0]:
            won = mine[0] > theirs[0]
        elif not mine[1] or not theirs[1] or mine[1] == theirs[1]:
            self.ties += 1
            return
        else:
            won = mine[1] < theirs[1]
        if won:
            self.wins += 1
        else:
            self.losses += 1

    def as_dict(self):
        def pct(n):
            return round(n * 100 / self.common) if self.common else 0
        return {
            'common': self.common, 'wins': self.wins, 'ties': self.ties, 'losses': self.losses,
            'wins_pct': pct(self.wins), 'ties_pct': pct(self.ties), 'losses_pct': pct(self.losses),
        }


# --- places -----------------------------------------------------------------

def _month_places(game, month, actor):
    """Places in the published monthly table, ordered exactly like the table."""
    from games.daily.registry import daily_leaderboard_sort
    from games.results.snapshots.aggregate_snapshot_read import snapshot_actor_place

    version_id = DailyAggregateSnapshot.objects.filter(
        game=game, month=month, published_version__isnull=False,
    ).values_list('published_version_id', flat=True).first()
    if version_id is None:
        return None
    by_attempts = daily_leaderboard_sort(game.pk, None) == 'attempts'
    place = snapshot_actor_place(
        version_id, actor.kind, actor.obj.pk, 'attempts' if by_attempts else 'time',
    )
    if place is None:
        return None
    place['tie_label'] = 'с учётом попыток' if by_attempts else 'с учётом времени'
    return place


# --- month view -------------------------------------------------------------

def _month_key(value):
    return value.strftime('%Y-%m')


def _month_label(value):
    return '{} {}'.format(MONTH_NAMES[value.month], value.year)


def played_months(actor, game):
    """``{first day of month: releases played}`` by completion date."""
    counts = defaultdict(int)
    for moment in PlayerCompletedGame.objects.filter(game=game, **actor.own).values_list('completed_at', flat=True):
        local = moment.astimezone(MOSCOW).date()
        counts[date(local.year, local.month, 1)] += 1
    return counts


def month_view(actor, game_id, month=None, *, viewer=None, now=None):
    """JSON-ready month of one game; ``None`` when the game has no releases."""
    from games.results.standings.aggregate_leaderboard import _monthly_columns

    definition = get_daily_game(game_id)
    if definition is None or not definition.capabilities.aggregate_results:
        return None
    game = Game.objects.filter(pk=game_id).first()
    if game is None:
        return None
    now = now or timezone.now()
    months_played = played_months(actor, game)
    if month in (None, '', 'latest'):
        today = now.astimezone(MOSCOW).date()
        current = date(today.year, today.month, 1)
        month = _month_key(current if current in months_played or not months_played else max(months_played))
    columns, selected, previous, following, _ = _monthly_columns(game, month)
    if not columns:
        return None
    columns = sorted(columns, key=lambda col: int(col.link.number))
    group_ids = [col.link.task_group_id for col in columns]
    task_type = definition.task_type
    mine = actor_releases(game, task_type, actor, group_ids)
    theirs = actor_releases(game, task_type, viewer, group_ids) if viewer is not None else None
    authored = authored_group_ids(actor, group_ids)
    baselines = release_baselines(game, task_type, columns, now=now)

    duel = Duel() if theirs is not None else None
    releases = []
    gained = possible = 0.0
    eligible = played = 0
    for col in columns:
        group_id = col.link.task_group_id
        max_score = float(col.max_score) if col.max_score else None
        own = mine.get(group_id)
        is_author = group_id in authored
        is_pre = bool(own and own['pre'])
        if not is_author and not is_pre:
            eligible += 1
            if max_score:
                possible += max_score
                if own and own['score'] is not None:
                    gained += own['score']
            if own:
                played += 1
        base = baselines[group_id]
        other = theirs.get(group_id) if theirs is not None else None
        if (duel is not None and own and other and own['score'] is not None and other['score'] is not None
                and not own['pre'] and not other['pre']):
            # Mirrors head_to_head: from the viewer's side.
            duel.add((other['score'], other['time_ms']), (own['score'], own['time_ms']))
        releases.append({
            'n': int(col.link.number),
            'date': col.published_at.astimezone(MOSCOW).date().isoformat() if col.published_at else None,
            'url': col.url.replace('results/', ''),
            'author': is_author,
            'pre': is_pre,
            'p': _series_point(own, max_score),
            'm': base,
            'v': _series_point(other, max_score) if theirs is not None else None,
        })
    month_names = sorted(set(months_played) | {selected})
    return {
        'game': game_id,
        'title': definition.title,
        'month': _month_key(selected),
        'month_label': _month_label(selected),
        'prev': _month_key(previous) if previous else None,
        'next': _month_key(following) if following else None,
        'months': [
            {'key': _month_key(m), 'label': _month_label(m), 'played': months_played.get(m, 0)}
            for m in reversed(month_names)
        ],
        'metrics': ['time'] + (['att'] if daily_game_counts_attempts(game_id) else []) + ['hints', 'score'],
        'kpi': {
            'places': _month_places(game, selected, actor),
            'score_pct': round(gained * 100 / possible) if possible else None,
            'played': played,
            'eligible': eligible,
            'authored': len(authored),
            'duel': duel.as_dict() if duel is not None else None,
        },
        'releases': releases,
    }


def _series_point(item, max_score):
    if not item:
        return None
    return {
        'time': item['time'],
        'att': item['att'],
        'hints': item['hints'],
        'score': (
            round(item['score'] * 100 / max_score, 1)
            if max_score and item['score'] is not None else None
        ),
        'solved': item['solved'],
    }
