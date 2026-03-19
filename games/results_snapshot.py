import datetime

from games.models import Attempt, GameResultsSnapshot


def _json_num(x):
    """
    Convert Decimal-like values to JSON-serializable numbers.
    Keep up to 3 decimals (project-wide convention).
    """
    if x is None:
        return 0
    try:
        f = float(x)
    except Exception:
        try:
            f = float(str(x).strip().replace(',', '.'))
        except Exception:
            return 0
    f = round(f, 3)
    if abs(f - round(f)) < 1e-9:
        return int(round(f))
    return f


class _SnapTask:
    def __init__(self, number):
        self.number = number


class _SnapTaskGroup:
    def __init__(self, number, name, tasks):
        self.number = number
        self.name = name
        self._tasks = tasks

    def get_n_tasks_for_results(self):
        return len(self._tasks)


class _SnapHint:
    def __init__(self, number):
        self.number = number


class _SnapHintAttempt:
    def __init__(self, number):
        self.hint = _SnapHint(number)
        self.is_real_request = True


class _SnapBestAttempt:
    def __init__(self, status):
        self.status = status


class _SnapAttemptsInfo:
    def __init__(self, best_status, n_attempts, result_points, sum_hint_penalty, hint_numbers):
        self.best_attempt = _SnapBestAttempt(best_status) if best_status else None
        self._n_attempts = int(n_attempts or 0)
        self._result_points = result_points or 0
        self._sum_hint_penalty = sum_hint_penalty or 0
        self.hint_attempts = [_SnapHintAttempt(n) for n in (hint_numbers or [])]
        self.attempts = [None] * self._n_attempts

    def get_n_attempts(self):
        return self._n_attempts

    def get_result_points(self):
        return max(0, (self._result_points or 0))

    def get_sum_hint_penalty(self):
        return max(0, (self._sum_hint_penalty or 0))


def snapshot_to_results_context(game, payload):
    """
    Convert snapshot payload into the same shape that results templates expect.
    Uses lightweight objects for AttemptsInfo and headers.
    """
    # Headers
    task_groups = []
    task_group_to_tasks = {}
    tasks_flat = []
    for tg in payload.get('task_groups') or []:
        tasks = [_SnapTask(t.get('number')) for t in (tg.get('tasks') or [])]
        tg_obj = _SnapTaskGroup(tg.get('number'), tg.get('name'), tasks)
        task_groups.append(tg_obj)
        task_group_to_tasks[tg_obj.number] = tasks
        tasks_flat.extend(tasks)

    # Teams: use real Team objects for name/links, but keep order from snapshot
    from games.models import Team
    rows = payload.get('rows') or []
    team_ids = [r.get('team_id') for r in rows if r.get('team_id')]
    teams_qs = Team.objects.filter(name__in=team_ids)
    teams_by_id = {t.pk: t for t in teams_qs}
    teams_sorted = [teams_by_id.get(tid) for tid in team_ids if tid in teams_by_id]

    team_to_score = {}
    team_to_place = {}
    team_to_max_best_time = {}
    team_to_list_attempts_info = {}
    team_to_cells = {}

    for row in rows:
        tid = row.get('team_id')
        team = teams_by_id.get(tid)
        if not team:
            continue
        team_to_score[team] = row.get('score') or 0
        team_to_place[team] = row.get('place') or 0
        t_iso = row.get('max_best_time')
        if t_iso:
            try:
                team_to_max_best_time[team] = datetime.datetime.fromisoformat(t_iso)
            except Exception:
                pass

        ais = []
        cells = []
        for cell in (row.get('cells') or []):
            if not cell:
                ais.append(None)
                cells.append({'ai': None, 'cls': 'cell-no'})
                continue
            ai_obj = _SnapAttemptsInfo(
                best_status=cell.get('best_status'),
                n_attempts=cell.get('n_attempts'),
                result_points=cell.get('result_points'),
                sum_hint_penalty=cell.get('sum_hint_penalty'),
                hint_numbers=cell.get('hint_numbers') or [],
            )
            ais.append(ai_obj)
            cells.append({'ai': ai_obj, 'cls': cell.get('cls') or 'cell-no'})

        team_to_list_attempts_info[team] = ais
        team_to_cells[team] = cells

    return {
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'teams_sorted': teams_sorted,
        'team_to_list_attempts_info': team_to_list_attempts_info,
        'team_to_cells': team_to_cells,
        'team_to_score': team_to_score,
        'team_to_place': team_to_place,
        'team_to_max_best_time': team_to_max_best_time,
        'snapshot_payload': payload,
    }


def build_results_snapshot_payload(game, mode='tournament'):
    """
    Snapshot payload is intentionally denormalized and self-sufficient for rendering:
    it contains task group/task headers, team order, and per-cell points/status/hints.

    Hint penalties are included because we rely on AttemptsInfo.get_result_points()
    and AttemptsInfo.get_sum_hint_penalty().
    """
    # Use the same ordering/filtering rules as results pages.
    from django.db.models import Q

    task_groups = sorted(game.task_groups.all(), key=lambda tg: tg.number)
    task_group_headers = []
    tasks_flat = []
    for tg in task_groups:
        tasks = sorted(tg.tasks.filter(~Q(task_type='text_with_forms')), key=lambda t: t.key_sort())
        task_group_headers.append({
            'number': tg.number,
            'name': tg.name,
            'n_tasks_for_results': tg.get_n_tasks_for_results(),
            'tasks': [{'number': t.number} for t in tasks],
        })
        for t in tasks:
            tasks_flat.append(t)

    team_to_score = {}  # {team_pk: score}
    team_to_max_best_time = {}  # {team_pk: datetime}
    team_task_to_cell = {}  # {(team_pk, task_id): cell}

    for task in tasks_flat:
        for ai in Attempt.manager.get_task_attempts_infos(task=task, mode=mode):
            if not (ai.attempts or ai.hint_attempts):
                continue
            team = None
            if ai.attempts:
                team = ai.attempts[0].team
            elif ai.hint_attempts:
                team = ai.hint_attempts[0].team
            if not team or team.is_hidden:
                continue

            team_pk = team.pk
            team_to_score.setdefault(team_pk, 0)
            task_points = 0
            best_status = None
            best_time = None
            if ai.best_attempt is not None:
                task_points = _json_num(ai.best_attempt.points or 0)
                best_status = ai.best_attempt.status
                best_time = ai.best_attempt.time

            sum_hint_penalty = _json_num(ai.get_sum_hint_penalty())
            result_points = _json_num(ai.get_result_points())
            n_attempts = ai.get_n_attempts()
            hint_numbers = sorted([ha.hint.number for ha in ai.hint_attempts if ha.is_real_request]) if ai.hint_attempts else []

            # For new UI results table coloring (cell-full/cell-some/...)
            try:
                max_points = float(task.get_points())
            except Exception:
                max_points = 0.0
            has_attempts = bool(n_attempts) or bool(hint_numbers)
            cls = 'cell-no'
            if max_points > 0 and float(result_points) >= max_points - 1e-9:
                cls = 'cell-full'
            elif float(result_points) > 0:
                cls = 'cell-some'
            elif has_attempts:
                cls = 'cell-zero'

            if task_points and task_points > 0:
                team_to_score[team_pk] = _json_num(team_to_score.get(team_pk, 0) + max(0, _json_num(task_points) - _json_num(sum_hint_penalty)))
                if best_time is not None:
                    prev = team_to_max_best_time.get(team_pk)
                    team_to_max_best_time[team_pk] = best_time if prev is None else max(prev, best_time)

            team_task_to_cell[(team_pk, task.id)] = {
                'best_status': best_status,
                'n_attempts': n_attempts,
                'result_points': _json_num(result_points),
                'sum_hint_penalty': _json_num(sum_hint_penalty),
                'hint_numbers': hint_numbers,
                'cls': cls,
            }

    # Sort teams by (-score, max_best_time, name)
    team_ids = list(team_to_score.keys())
    # Query only teams that appear in results
    from games.models import Team
    teams_qs = Team.objects.filter(name__in=team_ids)
    teams_by_id = {t.pk: t for t in teams_qs}

    def _team_sort_key(team_pk):
        team = teams_by_id.get(team_pk)
        score = _json_num(team_to_score.get(team_pk, 0))
        max_t = team_to_max_best_time.get(team_pk)
        if max_t is None:
            max_t = datetime.datetime.now()
        return (-score, max_t, str(team) if team is not None else str(team_pk))

    teams_sorted_ids = sorted(team_ids, key=_team_sort_key)

    team_to_place = {}
    for i, tid in enumerate(teams_sorted_ids):
        team_to_place[tid] = 1 + i
        if i:
            prev = teams_sorted_ids[i - 1]
            if team_to_score.get(tid, 0) == team_to_score.get(prev, 0):
                team_to_place[tid] = team_to_place[prev]

    # Build rows
    rows = []
    for tid in teams_sorted_ids:
        rows.append({
            'team_id': tid,
            'score': _json_num(team_to_score.get(tid, 0)),
            'place': team_to_place.get(tid, 0),
            'max_best_time': (team_to_max_best_time.get(tid).isoformat() if team_to_max_best_time.get(tid) else None),
            'cells': [team_task_to_cell.get((tid, task.id)) for task in tasks_flat],
        })

    return {
        'mode': mode,
        'game_id': game.id,
        'created_at': datetime.datetime.now().isoformat(),
        'task_groups': task_group_headers,
        'task_ids': [t.id for t in tasks_flat],
        'rows': rows,
    }


def freeze_game_results(game, mode='tournament', overwrite=False):
    obj = GameResultsSnapshot.objects.filter(game=game, mode=mode).first()
    if obj and not overwrite:
        # Snapshot already frozen; do not rebuild payload (expensive) or overwrite.
        return obj, False
    payload = build_results_snapshot_payload(game, mode=mode)
    if not obj:
        obj = GameResultsSnapshot(game=game, mode=mode)
    obj.payload = payload
    obj.save()
    return obj, True

