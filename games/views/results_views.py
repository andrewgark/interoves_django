import datetime
import json
from django.db.models import Q
from django.shortcuts import render, get_object_or_404
from django.views import defaults
from games.models import Game, Team, GameResultsSnapshot
from games.views.util import has_profile, has_team
from games.results_snapshot import snapshot_to_results_context


def results_page(request, game_id, mode='general'):
    game = get_object_or_404(Game, id=game_id)
    if has_profile(request.user) and request.user.profile.team_on and \
       not game.has_access('see_results', mode=mode, team=request.user.profile.team_on):
        return defaults.page_not_found(request)

    snap = GameResultsSnapshot.objects.filter(game=game, mode=mode).first()
    if snap and snap.payload:
        data = snapshot_to_results_context(game, snap.payload)
        return render(request, 'results.html', {
            'mode': mode,
            'game': game,
            **data,
        })

    team_to_list_attempts_info = {}
    team_to_score = {}
    team_to_max_best_time = {}
    team_task_to_attempts_info = {}

    class _ResultCol:
        __slots__ = ('number', 'name', '_n')

        def __init__(self, number, name, n_tasks):
            self.number = number
            self.name = name
            self._n = n_tasks

        def get_n_tasks_for_results(self):
            return self._n

    placements = sorted(
        game.task_group_links.select_related('task_group'),
        key=lambda p: p.number,
    )
    task_group_to_tasks = {}

    for p in placements:
        tg = p.task_group
        task_group_to_tasks[p.number] = sorted(
            tg.tasks.filter(~Q(task_type='text_with_forms')),
            key=lambda t: t.key_sort(),
        )
        for task in task_group_to_tasks[p.number]:
            from games.models import Attempt
            for attempts_info in Attempt.manager.get_task_attempts_infos(
                task=task, mode=mode, game=game,
            ):
                if attempts_info.attempts or attempts_info.hint_attempts:
                    if attempts_info.attempts:
                        team = attempts_info.attempts[0].team
                    else:
                        team = attempts_info.hint_attempts[0].team
                    if not team.is_hidden:
                        if team not in team_to_score:
                            team_to_score[team] = 0
                        task_points = 0
                        if attempts_info.best_attempt is not None:
                            task_points = attempts_info.best_attempt.points

                        if task_points > 0:
                            team_to_score[team] += max(0, task_points - attempts_info.get_sum_hint_penalty())
                            if team not in team_to_max_best_time:
                                team_to_max_best_time[team] = attempts_info.best_attempt.time
                            else:
                                team_to_max_best_time[team] = max(team_to_max_best_time[team], attempts_info.best_attempt.time)

                        team_task_to_attempts_info[(team, task)] = attempts_info

    for team in team_to_score.keys():
        for p in placements:
            for task in task_group_to_tasks[p.number]:
                if team not in team_to_list_attempts_info:
                    team_to_list_attempts_info[team] = []
                if (team, task) in team_task_to_attempts_info:
                    attempts_info = team_task_to_attempts_info[(team, task)]
                    team_to_list_attempts_info[team].append(attempts_info)
                else:
                    team_to_list_attempts_info[team].append(None)

    teams_sorted = []
    for team in team_to_score.keys():
        score = team_to_score[team]
        max_best_time = team_to_max_best_time.get(team,  datetime.datetime.now())
        teams_sorted.append((-score, max_best_time, team))
    teams_sorted = [team for anti_score, max_best_time, team in sorted(
        teams_sorted,
        key=lambda t: (t[0], t[1], str(t[2]))
    )]
    team_to_place = {}
    for i, team in enumerate(teams_sorted):
        team_to_place[team] = 1 + i
        if i:
            prev_team = teams_sorted[i - 1]
            if team_to_score[team] == team_to_score[prev_team]:
                team_to_place[team] = team_to_place[prev_team]

    # Per-cell CSS class for results table coloring.
    def _to_float(x):
        try:
            return float(x)
        except Exception:
            return 0.0

    def _has_pending(ai):
        try:
            attempts = getattr(ai, 'attempts', None) or []
            for a in attempts:
                if getattr(a, 'status', None) == 'Pending':
                    return True
        except Exception:
            pass
        return False

    tasks_flat = []
    for p in placements:
        for task in task_group_to_tasks[p.number]:
            tasks_flat.append(task)

    task_groups = [
        _ResultCol(p.number, p.name, len(task_group_to_tasks[p.number]))
        for p in placements
    ]

    team_to_cells = {}
    for team in teams_sorted:
        cells = []
        attempts_list = team_to_list_attempts_info.get(team, [])
        for idx, task in enumerate(tasks_flat):
            ai = attempts_list[idx] if idx < len(attempts_list) else None
            max_points = _to_float(getattr(task, 'get_results_max_points', lambda: getattr(task, 'points', 0))())
            points = 0.0
            has_attempts = False
            n_attempts = 0
            hint_numbers = []
            if ai:
                try:
                    n_attempts = int(ai.get_n_attempts() or 0)
                    has_attempts = n_attempts > 0
                except Exception:
                    has_attempts = False
                    n_attempts = 0
                try:
                    points = _to_float(ai.get_result_points())
                except Exception:
                    points = 0.0
                try:
                    hint_numbers = sorted([
                        ha.hint.number
                        for ha in (getattr(ai, 'hint_attempts', None) or [])
                        if getattr(ha, 'is_real_request', False)
                    ])
                except Exception:
                    hint_numbers = []

            cls = ''
            if has_attempts:
                if max_points > 0 and points >= max_points - 1e-9:
                    cls = 'cell-ok'
                elif points <= 0:
                    cls = 'cell-wrong'
                elif _has_pending(ai):
                    cls = 'cell-pending'
                else:
                    cls = 'cell-partial'
            cells.append({
                'cls': cls,
                'n_attempts': n_attempts,
                'result_points': points,
                'hint_numbers': hint_numbers,
            })
        team_to_cells[team] = cells

    if mode == 'tournament':
        game.results = json.dumps({
            team.name: {'score': str(score), 'place': team_to_place[team]} for team, score in team_to_score.items()
        })
        game.save()

    return render(request, 'results.html', {
        'mode': mode,
        'game': game,
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'teams_sorted': teams_sorted,
        'team_to_list_attempts_info': team_to_list_attempts_info,
        'team_to_cells': team_to_cells,
        'team_to_score': team_to_score,
        'team_to_place': team_to_place,
        'team_to_max_best_time': team_to_max_best_time,
    }) 