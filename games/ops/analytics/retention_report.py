"""Retention lines for the admin Telegram report (games.ops.telegram.bot.reports).

What the November 2026 analysis had to export by hand, recomputed from
backend rows on every report:

- regulars: actors with 3+ distinct play days in the last 7; at 6-7 days a
  week ~1% leave per week, at 3-4 days 10-14%, so the count and its flows
  (became regular / stopped playing) are the health of the core;
- newcomers: players whose first trusted game was 8-14 days before the
  report end, and how many came back on a later day within 7 days, split by
  first-touch source (games.ops.analytics.acquisition) once it is recorded;
- the hub entry A/B test (games.ops.analytics.hub_entry_experiment);
- likes on the period's puzzles: the share of likes is the one puzzle trait
  tied to newcomers coming back.

Players are user or anonymous actors; team rows are not people.
"""
from __future__ import annotations

import html
import logging
from datetime import timedelta

from django.db.models import Count, Min, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from games.models import Like, PlayerAnalyticsState, PlayerStartedGame
from games.ops.analytics.metrics import TRUSTED_IDENTITY_CUTOVER

logger = logging.getLogger('games.analytics.retention_report')

REGULAR_MIN_DAYS = 3
WINDOW = timedelta(days=7)
RETURN_DAYS = 7
DAILY_GAME_LABELS = {
    'salad': 'Салатик',
    'ladder': 'Лесенка',
    'alphabet': 'Алфавитка',
    'censorly': 'Цензурка',
}
LOW_LIKE_SHARE = 0.5
LOW_LIKE_MIN_VOTES = 10
TOP_SOURCES = 4


def _people():
    return PlayerStartedGame.objects.filter(
        is_backfilled=False, started_at__gte=TRUSTED_IDENTITY_CUTOVER, team__isnull=True,
    )


def _actor(user_id, anon_key):
    return ('u', user_id) if user_id else ('a', anon_key)


def _play_days(since, until):
    """{actor: {local date}} for starts in [since, until)."""
    tz = timezone.get_current_timezone()
    rows = (
        _people().filter(started_at__gte=since, started_at__lt=until)
        .annotate(day=TruncDate('started_at', tzinfo=tz))
        .values_list('user_id', 'anon_key', 'day').distinct()
    )
    days = {}
    for user_id, anon_key, day in rows:
        days.setdefault(_actor(user_id, anon_key), set()).add(day)
    return days


def regulars_at(end):
    """Actors with REGULAR_MIN_DAYS+ play days in the WINDOW before ``end``, and all actives."""
    days = _play_days(end - WINDOW, end)
    return {actor for actor, played in days.items() if len(played) >= REGULAR_MIN_DAYS}, set(days)


def regulars_lines(end):
    now_regulars, now_active = regulars_at(end)
    previous_regulars, _ = regulars_at(end - WINDOW)
    stayed = len(previous_regulars & now_regulars)
    lighter = len((previous_regulars & now_active) - now_regulars)
    gone = len(previous_regulars - now_active)
    became = len(now_regulars - previous_regulars)
    return [
        'Постоянные (3+ дня из 7): {} · неделю назад: {}'.format(len(now_regulars), len(previous_regulars)),
        'За неделю: остались {} · стали реже {} · перестали играть {} · новые постоянные {}'.format(
            stayed, lighter, gone, became,
        ),
    ]


def _earliest_starts(actors):
    """{actor: first trusted start time} for the given actors."""
    users = {value for kind, value in actors if kind == 'u'}
    anons = {value for kind, value in actors if kind == 'a'}
    earliest = {}
    if users:
        for user_id, first_at in (
            _people().filter(user_id__in=users, anon_key__isnull=True)
            .values('user_id').annotate(first_at=Min('started_at')).values_list('user_id', 'first_at')
        ):
            earliest[('u', user_id)] = first_at
    if anons:
        for anon_key, first_at in (
            _people().filter(anon_key__in=anons, user__isnull=True)
            .values('anon_key').annotate(first_at=Min('started_at')).values_list('anon_key', 'first_at')
        ):
            earliest[('a', anon_key)] = first_at
    return earliest


def newcomer_cohort(since, until):
    """{actor: returned?} for players whose first trusted game was in [since, until).

    Returned: began a game on a later local day within RETURN_DAYS.
    """
    tz = timezone.get_current_timezone()
    candidates = {
        _actor(user_id, anon_key)
        for user_id, anon_key in _people().filter(started_at__gte=since, started_at__lt=until)
        .values_list('user_id', 'anon_key').distinct()
    }
    earliest = _earliest_starts(candidates)
    cohort = {actor: first_at for actor, first_at in earliest.items() if since <= first_at < until}
    if not cohort:
        return {}
    later = _play_days(since, until + timedelta(days=RETURN_DAYS + 1))
    result = {}
    for actor, first_at in cohort.items():
        first_day = timezone.localtime(first_at, tz).date()
        last_day = first_day + timedelta(days=RETURN_DAYS)
        result[actor] = any(first_day < day <= last_day for day in later.get(actor, ()))
    return result


def _source_label(state):
    if state.first_utm_source:
        return state.first_utm_source + (' / ' + state.first_utm_campaign if state.first_utm_campaign else '')
    if state.first_referrer_host:
        return state.first_referrer_host
    return 'без метки'


def _sources(cohort):
    users = [value for kind, value in cohort if kind == 'u']
    anons = [value for kind, value in cohort if kind == 'a']
    states = PlayerAnalyticsState.objects.filter(
        Q(user_id__in=users, team__isnull=True, anon_key__isnull=True)
        | Q(anon_key__in=anons, team__isnull=True, user__isnull=True),
        first_touch_at__isnull=False,
    )
    return {_actor(state.user_id, state.anon_key): _source_label(state) for state in states}


def _pct(part, whole):
    return '{:.0f}%'.format(100 * part / whole) if whole else '—'


def newcomer_lines(end):
    cohort = newcomer_cohort(end - 2 * WINDOW, end - WINDOW)
    previous = newcomer_cohort(end - 3 * WINDOW, end - 2 * WINDOW)
    returned = sum(cohort.values())
    lines = ['Новички (первая игра 8–14 дней назад): {} · вернулись в течение недели: {} · когорта до них: {}'.format(
        len(cohort), _pct(returned, len(cohort)), _pct(sum(previous.values()), len(previous)),
    )]
    sources = _sources(cohort) if cohort else {}
    if sources:
        by_source = {}
        for actor, came_back in cohort.items():
            row = by_source.setdefault(sources.get(actor, 'до учёта источников'), [0, 0])
            row[0] += 1
            row[1] += int(came_back)
        ranked = sorted(by_source.items(), key=lambda item: -item[1][0])
        parts = ['{} {} ({})'.format(html.escape(label), n, _pct(back, n)) for label, (n, back) in ranked[:TOP_SOURCES]]
        lines.append('По источникам: ' + ' · '.join(parts))
    return lines


def hub_experiment_lines():
    from games.ops.analytics.hub_entry_experiment import experiment_summary

    summary = experiment_summary()
    if not any(row['assigned'] for row in summary.values()):
        return []
    labels = {'today': 'сегодняшний', 'first': 'для новичков'}
    lines = ['A/B главной (первый выпуск):']
    for variant, row in summary.items():
        lines.append('· {}: {} чел. · начали {} · доиграли первую {} · вернулись {} из {}'.format(
            labels.get(variant, variant), row['assigned'], _pct(row['started'], row['assigned']),
            _pct(row['finished_first'], row['started']), _pct(row['returned'], row['return_eligible']),
            row['return_eligible'],
        ))
    return lines


def _period_puzzles(since, until, *, per_game):
    """Most-played daily placements of the period: [(kind, public id, task group id)]."""
    rows = (
        _people().filter(started_at__gte=since, started_at__lt=until, game_kind__in=DAILY_GAME_LABELS)
        .values('game_kind', 'public_game_id', 'task_group_id').annotate(n=Count('id')).order_by('-n')
    )
    picked, taken = [], {}
    for row in rows:
        if taken.get(row['game_kind'], 0) >= per_game:
            continue
        taken[row['game_kind']] = taken.get(row['game_kind'], 0) + 1
        picked.append((row['game_kind'], row['public_game_id'], row['task_group_id']))
    return picked


def _puzzle_number(public_id):
    return str(public_id or '').rsplit(':', 1)[-1]


def likes_lines(since, until, *, weekly):
    puzzles = _period_puzzles(since, until, per_game=7 if weekly else 1)
    if not puzzles:
        return []
    votes = {
        row['task__task_group_id']: (row['likes'], row['dislikes'])
        for row in Like.manager.filter(task__task_group_id__in=[tg for _k, _p, tg in puzzles])
        .values('task__task_group_id')
        .annotate(likes=Count('pk', filter=Q(value=1)), dislikes=Count('pk', filter=Q(value=-1)))
    }
    entries = []
    for kind, public_id, task_group_id in puzzles:
        likes, dislikes = votes.get(task_group_id, (0, 0))
        total = likes + dislikes
        low = total >= LOW_LIKE_MIN_VOTES and likes / total < LOW_LIKE_SHARE
        if weekly and not low:
            continue
        entries.append('{} #{}: 👍{} 👎{}{}'.format(
            DAILY_GAME_LABELS[kind], _puzzle_number(public_id), likes, dislikes, ' 🔴' if low else '',
        ))
    if not entries:
        return []
    title = 'Выпуски недели с плохими оценками:' if weekly else 'Оценки выпусков:'
    return [title + ' ' + ' · '.join(entries)]


def report_lines(since, until, *, weekly=False):
    """The admin report's retention section; [] (and a log entry) on failure."""
    lines = ['<b>📈 Удержание</b>']
    for builder in (
        lambda: regulars_lines(until),
        lambda: newcomer_lines(until),
        hub_experiment_lines,
        lambda: likes_lines(since, until, weekly=weekly),
    ):
        try:
            lines.extend(builder())
        except Exception:
            logger.exception('retention report section failed')
    return lines if len(lines) > 1 else []
