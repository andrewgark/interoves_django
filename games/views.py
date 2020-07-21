from django.contrib.auth import get_user_model
from django.shortcuts import render, get_object_or_404
from django.views import defaults
from django.utils import timezone
from games.check import CheckerFactory
from games.forms import CreateTeamForm, JoinTeamForm, AttemptForm
from games.models import Team, Game, Attempt, AttemptsInfo, Task


def get_games_list():
    games_list = []
    for game in Game.objects.all():
        if game.is_ready:
            games_list.append(game)
    return sorted(games_list, key=lambda game: (game.start_time, game.name), reverse=True)


def main_page(request):
    return render(request, 'index.html', {
        'create_team_form': CreateTeamForm(),
        'join_team_form': JoinTeamForm(),
        'games': get_games_list(),
        'today': timezone.now()
    })


def has_profile(user):
    return user and user.profile


def create_team(request):
    user = request.user
    form = CreateTeamForm(request.POST)
    if form.is_valid() and has_profile(user) and not user.profile.team_on:
        team = form.save()
        user.profile.team_on = team
        user.profile.team_requested = None
        user.profile.save()
    return main_page(request)


def join_team(request):
    user = request.user
    form = JoinTeamForm(request.POST)
    if form.is_valid() and form.cleaned_data['name'] and \
       has_profile(user) and \
       not user.profile.team_on and not user.profile.team_requested:
        team = get_object_or_404(Team, name=form.cleaned_data['name'])
        user.profile.team_requested = team
        user.profile.save()
    return main_page(request)


def quit_from_team(request):
    user = request.user
    if has_profile(user):
        user.profile.team_on = None
        user.profile.team_requested = None
        user.profile.save()
    return main_page(request)


def process_user_request(request, user_id, action):
    active_user = request.user
    passive_user = get_object_or_404(get_user_model(), int(user_id))
    if has_profile(passive_user):
        if has_profile(active_user) and \
           active_user != passive_user and \
           active_user.profile.team_on == passive_user.profile.team_requested:
            passive_user.profile.team_requested = None
            if action == 'confirm':
                passive_user.profile.team_on = active_user.profile.team_on
            else:
                passive_user.profile.team_on = None
            passive_user.profile.save()
    return main_page(request)


def confirm_user_joining_team(request, user_id):
    return process_user_request(request, user_id, 'confirm')


def reject_user_joining_team(request, user_id):
    return process_user_request(request, user_id, 'reject')


def get_task_to_attempts_info(game, team, mode='general'):
    task_to_attempts_info = {}
    for task_group in game.task_groups.all():
        for task in task_group.tasks.all():
            attempts_info = AttemptsInfo.objects.filter(team=team, task=task, mode=mode)
            if attempts_info and attempts_info[0]:
                task_to_attempts_info[task.id] = attempts_info[0]
            else:
                task_to_attempts_info[task.id] = None
    return task_to_attempts_info


def game_page(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    if not has_profile(request.user) or not request.user.profile.team_on:
        return defaults.page_not_found()
    if not game.is_available(request.user.profile.team_on):
        return defaults.page_not_found()

    if 'tournament' in game.get_modes(Attempt(time=timezone.now())):
        mode = 'tournament'
    else:
        mode = 'general'

    task_to_attempts_info = get_task_to_attempts_info(game, request.user.profile.team_on, mode)
    
    task_groups = sorted(game.task_groups.all(), key=lambda tg: tg.number)

    task_group_to_tasks = {}
    for task_group in task_groups:
        task_group_to_tasks[task_group.number] = sorted(task_group.tasks.all(), key=lambda t: t.number)
    
    return render(request, 'game.html', {
        'game': game,
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'task_to_attempts_info': task_to_attempts_info,
        'mode': mode,
    })


def inherite_task_properties(task):
    task_group = task.task_group
    if task.checker:
        checker = task.checker
    else:
        checker = task_group.checker
    
    if task.points:
        points = task.points
    else:
        points = task_group.points

    if task.max_attempts:
        max_attempts = task.max_attempts
    else:
        max_attempts = task_group.max_attempts

    return checker, points, max_attempts


def better_status(first_status, second_status):
    status_to_key = {
        'Ok': 2,
        'Pending': 1,
        'Wrong': 0,
    }
    return status_to_key[first_status] > status_to_key[second_status]


def update_attempts_info(attempts_info, attempt):
    attempts_info.best_attempt = attempt
    list_attempts = sorted(attempts_info.attempts.all(), key=lambda x: x.time)  
    for attempt in list_attempts:
        if attempt.points > attempts_info.best_attempt.points or \
            (attempt.points == attempts_info.best_attempt.points and better_status(attempt.status, attempts_info.best_attempt.status)):
            attempts_info.best_attempt = attempt
    attempts_info.save()


def send_attempt(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    if not (has_profile(request.user) and request.user.profile.team_on):
        return main_page(request)
    team = request.user.profile.team_on
    game = task.task_group.game

    if not task.task_group.game.is_available(team):
        return defaults.page_not_found()

    form = AttemptForm(request.POST)
    if not form.is_valid():
        return game_page(request, game.id)

    attempt = form.save(commit=False)
    attempt.team = team
    attempt.task = task
    attempt.time = timezone.now()

    checker_type = task.get_checker()
    points = task.get_points()
    max_attempts = task.get_max_attempts()

    if checker_type is None or points is None or max_attempts is None:
        return game_page(request, game.id)

    modes = game.get_modes(attempt)
    attempts_infos = []

    for mode in modes:
        attempts_info_filter = AttemptsInfo.objects.filter(team=team, task=task, mode=mode)

        n_attempts = 0
        if attempts_info_filter and attempts_info_filter[0]:
            attempts_info = attempts_info_filter[0]
            n_attempts = len(attempts_info.attempts.all())
        else:
            attempts_info = AttemptsInfo(task=task, team=team, mode=mode)

        if mode == 'tournament' and n_attempts >= max_attempts:
            # отправлять ошибку, что слишком много посылок
            return game_page(request, game.id)

        if attempts_info_filter:
            for other_attempt in attempts_info.attempts.all():
                if attempt.text == other_attempt.text:
                    # отправлять ошибку, что это посылка - дубликат
                    return game_page(request, game.id)
        attempts_infos.append(attempts_info)

    checker = CheckerFactory().create_checker(checker_type, attempt.task.checker_data)
    attempt.status, attempt.points = checker.check(attempt.text)
    attempt.points *= points
    attempt.save(force_insert=True)

    for attempts_info in attempts_infos:
        attempts_info.save()
        update_attempts_info(attempts_info, attempt)
        attempt.attempts_infos.add(attempts_info)

    attempt.save(force_update=True)

    return game_page(request, game.id)


def results_page(request, game_id, mode='general'):
    game = get_object_or_404(Game, id=game_id)
    if has_profile(request.user) and request.user.profile.team_on and \
       not game.results_are_available(request.user.profile.team_on, mode):
        return defaults.page_not_found()

    team_to_list_attempts_info = {}
    team_to_score = {}
    team_to_max_best_time = {}

    task_groups = sorted(game.task_groups.all(), key=lambda tg: tg.number)
    task_group_to_tasks = {}

    for task_group in task_groups:
        task_group_to_tasks[task_group.number] = sorted(task_group.tasks.all(), key=lambda t: t.number)
        for task in task_group_to_tasks[task_group.number]:
            for attempts_info in AttemptsInfo.objects.filter(task=task, mode=mode):
                if attempts_info.best_attempt:
                    team = attempts_info.team

                    if team not in team_to_score:
                        team_to_score[team] = 0
                    team_to_score[team] += attempts_info.best_attempt.points

                    if team not in team_to_max_best_time:
                        team_to_max_best_time[team] = max(attempts_info.best_attempt.time)
                    else:
                        team_to_max_best_time[team] = max(attempts_info.best_attempt.time)

                    if team not in team_to_list_attempts_info:
                        team_to_list_attempts_info[team] = []
                    team_to_list_attempts_info[team].append(attempts_info)

    teams_sorted = []
    for team in team_to_score.keys():
        score = team_to_score[team]
        max_best_time = team_to_max_best_time[team]
        teams_sorted.append((score, max_best_time, team))
    teams_sorted = [team for score, max_best_time, team in sorted(teams_sorted)]

    return render(request, 'results.html', {
        'mode': mode,
        'game': game,
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'teams': teams,
        'team_to_list_attempts_info': team_to_list_attempts_info,
        'team_to_score': team_to_score,
        'team_to_max_best_time': team_to_max_best_time,
    })


def get_tournament_results(request, game_id):
    return results_page(request, game_id, mode='tournament')
