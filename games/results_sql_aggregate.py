"""
DB-side aggregation for general-mode results cells.

Instead of hydrating ~100k Attempt ORM rows, we:
  1) aggregate attempt best/count per (task, actor) in SQL (window + group by);
  2) load only real hint-attempt value rows (~10k) and sum penalties in Python
     (raddle in-game assists excluded from penalty, same as AttemptsInfo).

Returns the same shape as ``Attempt.manager.get_bulk_game_actor_rows`` for
``mode='general'``: ``{task_id: [(actor, AttemptsInfo-like), ...]}``.
"""

from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.db.models import (
    Case,
    CharField,
    Count,
    F,
    IntegerField,
    Max,
    Q,
    Value,
    When,
    Window,
)
from django.db.models.functions import Cast, Coalesce, Concat, RowNumber

from games.models import (
    Attempt,
    ChainTaskState,
    Hint,
    HintAttempt,
    PersonalResultsParticipant,
    Team,
    hidden_anon_keys,
)
from games.raddle import is_raddle_in_game_assist_hint


def _actor_key_annotation():
    """Stable partition key: t:<id> | u:<id> | a:<anon_key>."""
    return Case(
        When(
            team_id__isnull=False,
            then=Concat(Value('t:'), Cast('team_id', CharField())),
        ),
        When(
            user_id__isnull=False,
            then=Concat(Value('u:'), Cast('user_id', CharField())),
        ),
        When(
            anon_key__isnull=False,
            then=Concat(Value('a:'), Coalesce(F('anon_key'), Value(''))),
        ),
        default=Value(''),
        output_field=CharField(),
    )


def _status_rank_annotation(field='status'):
    return Case(
        When(**{field: 'Ok'}, then=Value(3)),
        When(**{field: 'Partial'}, then=Value(2)),
        When(**{field: 'Pending'}, then=Value(1)),
        When(**{field: 'Wrong'}, then=Value(0)),
        default=Value(-1),
        output_field=IntegerField(),
    )


def _hint_actor_key_annotation():
    return Case(
        When(
            team_id__isnull=False,
            then=Concat(Value('t:'), Cast('team_id', CharField())),
        ),
        When(
            user_id__isnull=False,
            then=Concat(Value('u:'), Cast('user_id', CharField())),
        ),
        When(
            anon_key__isnull=False,
            then=Concat(Value('a:'), Coalesce(F('anon_key'), Value(''))),
        ),
        default=Value(''),
        output_field=CharField(),
    )


class _AggHintAttempt:
    __slots__ = ('is_real_request', 'hint')

    def __init__(self, number):
        self.is_real_request = True
        self.hint = SimpleNamespace(number=number, key_sort=lambda n=number: Hint.number_key(n) if n is not None else ())


class AggregatedAttemptsInfo:
    """Duck-types AttemptsInfo for results snapshot / cell builders."""

    __slots__ = (
        'best_attempt',
        'attempts',
        'hint_attempts',
        '_n_attempts',
        '_sum_hint_penalty',
    )

    def __init__(
        self,
        *,
        best_points,
        best_status,
        best_time,
        n_attempts,
        sum_hint_penalty,
        hint_numbers,
        has_pending,
    ):
        self._n_attempts = int(n_attempts or 0)
        self._sum_hint_penalty = sum_hint_penalty or 0
        if self._n_attempts:
            self.best_attempt = SimpleNamespace(
                points=best_points if best_points is not None else 0,
                status=best_status or '',
                time=best_time,
            )
            # Truthy sequence; Pending probe used by snapshot (cls ignores it today).
            if has_pending:
                self.attempts = [SimpleNamespace(status='Pending')]
            else:
                self.attempts = [SimpleNamespace(status=best_status or 'Wrong')]
        else:
            self.best_attempt = None
            self.attempts = []
        self.hint_attempts = [_AggHintAttempt(n) for n in (hint_numbers or [])]

    def get_n_attempts(self):
        return self._n_attempts

    def get_sum_hint_penalty(self):
        return self._sum_hint_penalty

    def get_result_points(self):
        points = 0
        if self.best_attempt is not None:
            points = self.best_attempt.points or 0
        try:
            points = float(points)
        except (TypeError, ValueError):
            points = 0
        try:
            penalty = float(self._sum_hint_penalty or 0)
        except (TypeError, ValueError):
            penalty = 0
        return max(0, points - penalty)

    def get_result_attempt(self):
        return self.best_attempt

    def get_hint_numbers(self):
        return [ha.hint.number for ha in self.hint_attempts]


def _parse_actor_key(actor_key):
    if not actor_key or ':' not in actor_key:
        return None
    kind, raw = actor_key.split(':', 1)
    if kind == 't' and raw != '':
        # Team PK is a string name in this project.
        return ('team', raw)
    if kind == 'u':
        try:
            return ('user', int(raw))
        except (TypeError, ValueError):
            return None
    if kind == 'a' and raw:
        return ('anon', raw)
    return None


def _attempt_actor_specs():
    """Native actor columns with the same team → user → anon precedence as actor_key."""
    return (
        ('team', 'team_id', Q(team_id__isnull=False)),
        (
            'user',
            'user_id',
            Q(team_id__isnull=True, user_id__isnull=False),
        ),
        (
            'anon',
            'anon_key',
            Q(team_id__isnull=True, user_id__isnull=True, anon_key__isnull=False)
            & ~Q(anon_key=''),
        ),
    )


def _actor_key_from_kind(kind, raw):
    prefix = {'team': 't', 'user': 'u', 'anon': 'a'}[kind]
    return '{}:{}'.format(prefix, raw)


def get_sql_aggregated_game_actor_rows(task_ids, game=None):
    """
    General-mode standings cells via SQL aggregates.

    Same return shape as ``get_bulk_game_actor_rows(..., mode='general')``.
    Does not apply tournament attempt windows. Skips alphabetty letter-hint
    penalty (callers with alphabetty tasks should keep the ORM bulk path).
    """
    if not task_ids:
        return {}

    task_ids = list(task_ids)
    hidden_anons = hidden_anon_keys()

    attempt_base = Attempt.manager.filter(task_id__in=task_ids, skip=False)
    if game is not None:
        attempt_base = attempt_base.filter(game=game)

    # Query each native actor column separately. The previous CASE/CONCAT
    # partition key prevented MySQL from using the existing task+actor indexes
    # and dominated slow-query logs on large games.
    count_rows = []
    best_by = {}
    for kind, actor_field, actor_filter in _attempt_actor_specs():
        actor_counts = (
            attempt_base.filter(actor_filter)
            .values('task_id', actor_field)
            .annotate(
                n_attempts=Count('id'),
                has_pending=Max(
                    Case(
                        When(status='Pending', then=Value(1)),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                ),
            )
        )
        for row in actor_counts:
            actor_key = _actor_key_from_kind(kind, row[actor_field])
            count_rows.append({
                'task_id': row['task_id'],
                'actor_key': actor_key,
                'team_id': row[actor_field] if kind == 'team' else None,
                'user_id': row[actor_field] if kind == 'user' else None,
                'anon_key': row[actor_field] if kind == 'anon' else None,
                'n_attempts': row['n_attempts'],
                'has_pending': row['has_pending'],
            })

        best_qs = (
            attempt_base.filter(actor_filter)
            .annotate(
                status_rank=_status_rank_annotation(),
                rn=Window(
                    expression=RowNumber(),
                    partition_by=[F('task_id'), F(actor_field)],
                    order_by=[
                        F('points').desc(),
                        F('status_rank').desc(),
                        F('time').asc(),
                    ],
                ),
            )
            .filter(rn=1)
            .values('task_id', actor_field, 'game_id', 'points', 'status', 'time')
        )
        for row in best_qs:
            actor_key = _actor_key_from_kind(kind, row[actor_field])
            best_by[(row['task_id'], actor_key)] = row

    # --- hint attempts: small volume; mirror AttemptsInfo penalty + hint_numbers ---
    hint_rows = list(
        HintAttempt.objects.filter(
            hint__task_id__in=task_ids,
            is_real_request=True,
        )
        .annotate(actor_key=_hint_actor_key_annotation())
        .exclude(actor_key='')
        .values(
            'hint__task_id',
            'actor_key',
            'hint__number',
            'hint__desc',
            'hint__points_penalty',
        )
    )

    hint_penalty = defaultdict(float)
    hint_numbers = defaultdict(list)
    for hr in hint_rows:
        task_id = hr['hint__task_id']
        actor_key = hr['actor_key']
        key = (task_id, actor_key)
        number = hr['hint__number']
        hint_numbers[key].append(number)
        # Penalty excludes raddle in-game assists (same as AttemptsInfo).
        fake_hint = SimpleNamespace(desc=hr['hint__desc'])
        if is_raddle_in_game_assist_hint(fake_hint):
            continue
        pen = hr['hint__points_penalty']
        if pen is None:
            continue
        try:
            hint_penalty[key] += float(pen)
        except (TypeError, ValueError):
            pass

    for key, nums in hint_numbers.items():
        hint_numbers[key] = sorted(nums, key=lambda n: Hint.number_key(n) if n is not None else ())

    # Alphabetty letter hints live in ChainTaskState rather than HintAttempt.
    # Load them once for all result cells.  The legacy ORM path looks up the
    # state for the game of the best attempt, so keep game_id in the key to
    # preserve that behaviour when shared TaskGroups are aggregated unscoped.
    from games.alphabetty.play import (
        alphabetty_hint_penalty_points,
        hint_count,
        load_state,
    )

    chain_state_qs = ChainTaskState.objects.filter(
        task_id__in=task_ids,
        task__task_type='alphabetty',
        game_mode='general',
    )
    if game is not None:
        chain_state_qs = chain_state_qs.filter(game=game)

    alphabetty_penalty = {}
    for row in chain_state_qs.values(
        'task_id', 'game_id', 'team_id', 'user_id', 'anon_key', 'state',
    ):
        if (
            row['team_id'] is not None
            and row['user_id'] is None
            and row['anon_key'] is None
        ):
            actor_key = 't:{}'.format(row['team_id'])
        elif (
            row['user_id'] is not None
            and row['team_id'] is None
            and row['anon_key'] is None
        ):
            actor_key = 'u:{}'.format(row['user_id'])
        elif row['anon_key'] and row['team_id'] is None and row['user_id'] is None:
            actor_key = 'a:{}'.format(row['anon_key'])
        else:
            continue
        alphabetty_penalty[(
            row['task_id'], actor_key, row['game_id'],
        )] = alphabetty_hint_penalty_points(
            hint_count(load_state(row['state']))
        )

    # Actors that only have hints (no attempts) still need a row.
    actors_by_task = defaultdict(dict)  # task_id -> actor_key -> meta
    for row in count_rows:
        actors_by_task[row['task_id']][row['actor_key']] = {
            'team_id': row['team_id'],
            'user_id': row['user_id'],
            'anon_key': row['anon_key'],
            'n_attempts': row['n_attempts'],
            'has_pending': bool(row['has_pending']),
        }
    for (task_id, actor_key) in list(hint_penalty.keys()) + list(hint_numbers.keys()):
        if actor_key not in actors_by_task[task_id]:
            parsed = _parse_actor_key(actor_key)
            if parsed is None:
                continue
            kind, raw = parsed
            actors_by_task[task_id][actor_key] = {
                'team_id': raw if kind == 'team' else None,
                'user_id': raw if kind == 'user' else None,
                'anon_key': raw if kind == 'anon' else None,
                'n_attempts': 0,
                'has_pending': False,
            }

    team_ids = set()
    user_ids = set()
    for task_map in actors_by_task.values():
        for meta in task_map.values():
            if meta['team_id'] is not None:
                team_ids.add(meta['team_id'])
            if meta['user_id'] is not None:
                user_ids.add(meta['user_id'])

    teams = {t.pk: t for t in Team.objects.filter(pk__in=team_ids)}
    users = {u.pk: u for u in User.objects.filter(pk__in=user_ids)} if user_ids else {}

    result = {tid: [] for tid in task_ids}
    for task_id, task_map in actors_by_task.items():
        rows_out = []
        for actor_key, meta in task_map.items():
            parsed = _parse_actor_key(actor_key)
            if parsed is None:
                continue
            kind, raw = parsed
            if kind == 'team':
                team = teams.get(raw)
                if team is None or team.is_hidden:
                    continue
                actor = team
            elif kind == 'user':
                user = users.get(raw)
                if user is None:
                    continue
                actor = PersonalResultsParticipant(user=user)
            else:
                if raw in hidden_anons:
                    continue
                actor = PersonalResultsParticipant(anon_key=raw)

            best = best_by.get((task_id, actor_key))
            key = (task_id, actor_key)
            letter_penalty = 0
            if best is not None and best.get('game_id') is not None:
                letter_penalty = alphabetty_penalty.get(
                    (task_id, actor_key, best['game_id']), 0,
                )
            ai = AggregatedAttemptsInfo(
                best_points=best['points'] if best else None,
                best_status=best['status'] if best else None,
                best_time=best['time'] if best else None,
                n_attempts=meta['n_attempts'],
                sum_hint_penalty=hint_penalty.get(key, 0) + letter_penalty,
                hint_numbers=hint_numbers.get(key, []),
                has_pending=meta['has_pending'],
            )
            if not (ai.attempts or ai.hint_attempts):
                continue
            rows_out.append((actor, ai))
        result[task_id] = rows_out

    return result


def tasks_need_orm_results_aggregate(tasks):
    """Scores that require the complete attempt history stay on the ORM path."""
    for t in tasks:
        if getattr(t, 'task_type', None) == 'word_salad':
            return True
    return False
