import json
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Set, Tuple
from types import SimpleNamespace

from django.db.models import Q

from games.models import Attempt, HintAttempt, Task
from games.util import better_status


EPS = 1e-9


@dataclass(frozen=True)
class Actor:
    """
    Single actor bucket for attempts/hints.
    Exactly one of (team_id, user_id, anon_key) must be set.
    """

    team_id: Optional[str] = None
    user_id: Optional[int] = None
    anon_key: Optional[str] = None

    def filter_kwargs(self) -> Dict:
        if self.team_id is not None:
            return {"team_id": self.team_id, "user__isnull": True, "anon_key__isnull": True}
        if self.user_id is not None:
            return {"user_id": self.user_id, "team__isnull": True, "anon_key__isnull": True}
        if self.anon_key is not None:
            return {"anon_key": self.anon_key, "team__isnull": True, "user__isnull": True}
        raise ValueError("Actor: pass team_id, user_id, or anon_key")


def task_effective_max_points(task: Task) -> float:
    try:
        return float(task.get_results_max_points())
    except Exception:
        try:
            return float(task.get_points())
        except Exception:
            return 0.0


def _best_attempt(attempts: Iterable[Attempt]) -> Optional[Attempt]:
    best = None
    for a in attempts or []:
        if best is None:
            best = a
            continue
        if (a.points or 0) > (best.points or 0):
            best = a
            continue
        if (a.points or 0) == (best.points or 0) and better_status(a.status, best.status):
            best = a
    return best


def _sum_hint_penalty(hint_attempts: Iterable[HintAttempt]) -> float:
    from games.raddle import is_raddle_in_game_assist_hint
    total = 0.0
    for ha in hint_attempts or []:
        try:
            if not getattr(ha, "is_real_request", False):
                continue
            hint = getattr(ha, "hint", None)
            if hint is None:
                continue
            if is_raddle_in_game_assist_hint(hint):
                continue
            total += float(getattr(hint, "points_penalty", 0) or 0)
        except Exception:
            continue
    return max(0.0, total)


def actor_task_result_points(
    *,
    task: Task,
    actor: Actor,
    mode: str = "general",
    game=None,
    include_other_games: bool = False,
) -> Tuple[float, bool]:
    """
    Returns (result_points, has_attempts_or_hints).

    - result_points = max(0, best_attempt.points - sum_hint_penalty)
    - best_attempt is chosen by max(points), then by better_status

    game is used for tournament-mode filtering and for scoping attempts/hints unless include_other_games=True.
    """
    task_ids = [task.id] if task and task.id else []
    out = bulk_actor_task_result_points(
        task_ids=task_ids,
        actor=actor,
        mode=mode,
        game=game,
        include_other_games=include_other_games,
    )
    if not task_ids:
        return 0.0, False
    pts, has_any, _status = out.get(task.id, (0.0, False, None))
    return pts, has_any


def bulk_actor_task_result_points(
    *,
    task_ids: Iterable[int],
    actor: Actor,
    mode: str = "general",
    game=None,
    include_other_games: bool = False,
) -> Dict[int, Tuple[float, bool, Optional[str]]]:
    """
    Bulk compute per-task result points for one actor.

    Returns: {task_id: (result_points, has_attempts_or_hints, best_status)}.
    """
    task_ids = [int(tid) for tid in (task_ids or []) if tid is not None]
    if not task_ids:
        return {}

    if mode == "general":
        return _bulk_actor_task_result_points_sql(
            task_ids=task_ids,
            actor=actor,
            game=game,
            include_other_games=include_other_games,
        )

    return _bulk_actor_task_result_points_orm(
        task_ids=task_ids,
        actor=actor,
        mode=mode,
        game=game,
        include_other_games=include_other_games,
    )


def _bulk_actor_task_result_points_sql(
    *,
    task_ids: list,
    actor: Actor,
    game=None,
    include_other_games: bool = False,
) -> Dict[int, Tuple[float, bool, Optional[str]]]:
    """SQL best-attempt + hint penalty for one actor (general mode)."""
    from django.db.models import Case, F, IntegerField, Max, Value, When, Window
    from django.db.models.functions import RowNumber

    from games.raddle import is_raddle_in_game_assist_hint

    att_qs = Attempt.manager.filter(task_id__in=task_ids, skip=False).filter(**actor.filter_kwargs())
    if (game is not None) and (not include_other_games):
        att_qs = att_qs.filter(game=game)

    status_rank = Case(
        When(status='Ok', then=Value(3)),
        When(status='Partial', then=Value(2)),
        When(status='Pending', then=Value(1)),
        When(status='Wrong', then=Value(0)),
        default=Value(-1),
        output_field=IntegerField(),
    )
    best_rows = (
        att_qs.annotate(
            status_rank=status_rank,
            rn=Window(
                expression=RowNumber(),
                partition_by=[F('task_id')],
                order_by=[
                    F('points').desc(),
                    F('status_rank').desc(),
                    F('time').asc(),
                ],
            ),
        )
        .filter(rn=1)
        .values('task_id', 'points', 'status')
    )
    best_by = {r['task_id']: r for r in best_rows}

    has_attempt_tasks = set(
        att_qs.values_list('task_id', flat=True).distinct()
    )

    hint_rows = list(
        HintAttempt.objects.filter(hint__task_id__in=task_ids, is_real_request=True)
        .filter(**actor.filter_kwargs())
        .values('hint__task_id', 'hint__desc', 'hint__points_penalty')
    )
    penalties: Dict[int, float] = {tid: 0.0 for tid in task_ids}
    has_hint_tasks: Set[int] = set()
    for hr in hint_rows:
        tid = hr['hint__task_id']
        if tid not in penalties:
            continue
        has_hint_tasks.add(tid)
        if is_raddle_in_game_assist_hint(SimpleNamespace(desc=hr['hint__desc'])):
            continue
        try:
            penalties[tid] += float(hr['hint__points_penalty'] or 0)
        except (TypeError, ValueError):
            pass

    result: Dict[int, Tuple[float, bool, Optional[str]]] = {}
    for tid in task_ids:
        best = best_by.get(tid)
        best_points = float(best['points'] or 0) if best else 0.0
        best_status = best['status'] if best else None
        penalty = penalties.get(tid, 0.0)
        pts = max(0.0, best_points - penalty)
        has_any = (tid in has_attempt_tasks) or (tid in has_hint_tasks)
        result[tid] = (pts, has_any, best_status)
    return result


def _bulk_actor_task_result_points_orm(
    *,
    task_ids: list,
    actor: Actor,
    mode: str = "general",
    game=None,
    include_other_games: bool = False,
) -> Dict[int, Tuple[float, bool, Optional[str]]]:
    # Attempts
    att_qs = Attempt.manager.filter(task_id__in=task_ids, skip=False).filter(**actor.filter_kwargs())
    if (game is not None) and (not include_other_games):
        att_qs = att_qs.filter(game=game)
    att_qs = att_qs.select_related("task", "game").order_by("time")
    attempts = list(att_qs)

    # Hint attempts
    hint_qs = (
        HintAttempt.objects.filter(hint__task_id__in=task_ids)
        .filter(**actor.filter_kwargs())
        .select_related("hint", "hint__task")
        .order_by("time")
    )
    hint_attempts = list(hint_qs)

    if mode == "tournament":
        from games.models import Attempt as AttemptModel

        attempts = AttemptModel.manager.filter_attempts_with_mode(attempts, mode, hint_game=game)
        attempts = list(attempts)
        hint_attempts = AttemptModel.manager.filter_attempts_with_mode(
            hint_attempts, mode, is_hint_attempts=True, hint_game=game
        )
        hint_attempts = list(hint_attempts)

    task_to_attempts: Dict[int, list] = {tid: [] for tid in task_ids}
    for a in attempts:
        if a.task_id in task_to_attempts:
            task_to_attempts[a.task_id].append(a)

    task_to_hints: Dict[int, list] = {tid: [] for tid in task_ids}
    for ha in hint_attempts:
        tid = getattr(getattr(ha, "hint", None), "task_id", None)
        if tid in task_to_hints:
            task_to_hints[tid].append(ha)

    result: Dict[int, Tuple[float, bool, Optional[str]]] = {}
    for tid in task_ids:
        att = task_to_attempts.get(tid, [])
        hints = task_to_hints.get(tid, [])
        best = _best_attempt(att)
        best_points = float(getattr(best, "points", 0) or 0) if best is not None else 0.0
        best_status = getattr(best, "status", None) if best is not None else None
        penalty = _sum_hint_penalty(hints)
        pts = max(0.0, best_points - penalty)
        has_any = bool(att) or bool(hints)
        result[tid] = (pts, has_any, best_status)
    return result


def bulk_actor_solved_task_ids(
    *,
    tasks: Iterable[Task],
    actor: Actor,
    mode: str = "general",
    game=None,
    include_other_games: bool = False,
) -> Set[int]:
    tasks = list(tasks or [])
    if not tasks:
        return set()
    task_ids = [t.id for t in tasks if t and t.id]
    pts_map = bulk_actor_task_result_points(
        task_ids=task_ids,
        actor=actor,
        mode=mode,
        game=game,
        include_other_games=include_other_games,
    )
    solved: Set[int] = set()
    for t in tasks:
        pts, _has_any, best_status = pts_map.get(t.id, (0.0, False, None))
        # Historical completion is monotonic.  After an author edit it is valid
        # to have status Ok with fewer points than the task's current maximum.
        if best_status == "Ok":
            solved.add(t.id)
            continue
        # get_results_max_points() parses replacements_lines text (~60ms/task on prod).
        # Skip when the actor has no scored progress on this task.
        if pts <= EPS:
            continue
        mp = task_effective_max_points(t)
        if mp > 0 and pts >= mp - EPS:
            solved.add(t.id)
    return solved
