import datetime
import json
from django.db.models import Q
from django.shortcuts import render, get_object_or_404
from django.views import defaults
from games.models import Game, Team
from games.views.util import has_profile, has_team


def results_page(request, game_id, mode='general'):
    game = get_object_or_404(Game, id=game_id)
    if has_profile(request.user) and request.user.profile.team_on and \
       not game.has_access('see_results', mode=mode, team=request.user.profile.team_on):
        return defaults.page_not_found(request)

    team_to_list_attempts_info = {}
    team_to_score = {}
    team_to_max_best_time = {}
    team_task_to_attempts_info = {}

    task_groups = sorted(game.task_groups.all(), key=lambda tg: tg.number)
    task_group_to_tasks = {}

    for task_group in task_groups:
        task_group_to_tasks[task_group.number] = sorted(
            task_group.tasks.filter(~Q(task_type='text_with_forms')) # исключаем задания этого типа из таблички
        , key=lambda t: t.key_sort())
        for task in task_group_to_tasks[task_group.number]:
            from games.models import Attempt
            for attempts_info in Attempt.manager.get_task_attempts_infos(task=task, mode=mode):
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
        for task_group in task_groups:
            for task in task_group_to_tasks[task_group.number]:
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
        'team_to_score': team_to_score,
        'team_to_place': team_to_place,
        'team_to_max_best_time': team_to_max_best_time,
    }) 