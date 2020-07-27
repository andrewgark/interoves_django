from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views import defaults
from django.utils import timezone
from games.check import CheckerFactory
from games.exception import *
from games.forms import CreateTeamForm, JoinTeamForm, AttemptForm
from games.models import Team, Game, Attempt, AttemptsInfo, Task


def get_games_list(request):
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on

    games_list = []
    for game in Game.objects.all():
        if game.is_ready or (game.is_testing and team and team.is_tester):
            games_list.append(game)
    return sorted(games_list, key=lambda game: (game.start_time, game.name), reverse=True)


def main_page(request):
    return render(request, 'index.html', {
        'create_team_form': CreateTeamForm(),
        'join_team_form': JoinTeamForm(),
        'games': get_games_list(request),
        'today': timezone.now()
    })


def has_profile(user):
    return user and getattr(user, 'profile', None)


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
    passive_user = get_object_or_404(get_user_model(), id=int(user_id))
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
    if not has_profile(request.user):
        raise UserHasNoProfileException('User {} has no profile'.format(request.user))
    if not request.user.profile.team_on:
        return PlayGameWithoutTeamException('User {} tries to sent attempt but has no team'.format(request.user.profile))
    if not game.is_available(request.user.profile.team_on):
        raise NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    mode = game.get_current_mode(Attempt(time=timezone.now()))

    task_to_attempts_info = get_task_to_attempts_info(game, request.user.profile.team_on, mode)
    
    task_groups = sorted(game.task_groups.all(), key=lambda tg: tg.number)

    task_group_to_tasks = {}
    for task_group in task_groups:
        task_group_to_tasks[task_group.number] = sorted(task_group.tasks.all(), key=lambda t: t.key_sort())
    
    return render(request, 'game.html', {
        'game': game,
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'task_to_attempts_info': task_to_attempts_info,
        'mode': mode,
    })


def better_status(first_status, second_status):
    status_to_key = {
        'Ok': 3,
        'Partial': 2,
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


def process_send_attempt(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    if not has_profile(request.user):
        raise UserHasNoProfileException('User {} has no profile'.format(request.user))
    if not request.user.profile.team_on:
        return PlayGameWithoutTeamException('User {} tries to sent attempt but has no team'.format(request.user.profile))

    team = request.user.profile.team_on
    game = task.task_group.game

    if not task.task_group.game.is_available(team):
        return NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    form = AttemptForm(request.POST)
    if not form.is_valid():
        return InvalidFormException('attempt form {} is not valid'.format(form))

    attempt = form.save(commit=False)
    attempt.team = team
    attempt.task = task
    attempt.time = timezone.now()

    checker_type = task.get_checker()
    points = task.get_points()
    max_attempts = task.get_max_attempts()

    modes = game.get_modes(attempt)
    attempts_infos = []
    
    current_mode = game.get_current_mode(attempt)
    current_attempts_info = None

    for mode in modes:
        attempts_info_filter = AttemptsInfo.objects.filter(team=team, task=task, mode=mode)

        n_attempts = 0
        if attempts_info_filter and attempts_info_filter[0]:
            attempts_info = attempts_info_filter[0]
            n_attempts = len(attempts_info.attempts.all())
        else:
            attempts_info = AttemptsInfo(task=task, team=team, mode=mode)

        if mode == 'tournament' and n_attempts >= max_attempts:
            raise TooManyAttemptsException('Team {} exceeds attempts limit ({}) in task {}'.format(team, max_attempts, task))

        if attempts_info_filter:
            for other_attempt in attempts_info.attempts.all():
                if attempt.text == other_attempt.text:
                    raise DuplicateAttemptException('Attempt duplicates one of the previous attempts by this team')
        attempts_infos.append(attempts_info)
        if mode == current_mode:
            current_attempts_info = attempts_info
    assert current_attempts_info is not None

    checker = CheckerFactory().create_checker(checker_type, attempt.task.checker_data)
    attempt.status, attempt.points = checker.check(attempt.text)
    if 'tournament' in modes and attempt.status != 'Ok':
        attempt.possible_status = attempt.status
        attempt.status = 'Pending'
    attempt.points *= points
    attempt.save(force_insert=True)

    for attempts_info in attempts_infos:
        attempts_info.save()
        update_attempts_info(attempts_info, attempt)
        attempt.attempts_infos.add(attempts_info)

    attempt.save(force_update=True)
    return {
        'status': 'ok',
        'task_id': task.id,
        'html': render(request, 'task.html', {
            'task': task,
            'task_group': task.task_group,
            'attempts_info': current_attempts_info,
            'mode': current_mode,
        }).content.decode('UTF-8'),
    }


def send_attempt(request, task_id):
    try:
        response = process_send_attempt(request, task_id)
    except DuplicateAttemptException:
        response = {'status': 'duplicate'}
    except TooManyAttemptsException:
        response = {'status': 'attempt_limit_exceeded'}
    return JsonResponse(response)


def get_answer(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    if not has_profile(request.user):
        raise UserHasNoProfileException('User {} has no profile'.format(request.user))
    if not request.user.profile.team_on:
        return PlayGameWithoutTeamException('User {} tries to sent attempt but has no team'.format(request.user.profile))

    team = request.user.profile.team_on
    game = task.task_group.game

    if not task.task_group.game.is_available(team):
        return NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    mode = game.get_current_mode(Attempt(time=timezone.now()))

    if mode != 'general':
        return NoAnswerAccessException('User {} has no access to answers to game {} right now'.format(request.user.profile, game))

    return JsonResponse({
        'html': render(request, 'answer.html', {
            'task': task,
        }).content.decode('UTF-8'),
    })


def results_page(request, game_id, mode='general'):
    game = get_object_or_404(Game, id=game_id)
    if has_profile(request.user) and request.user.profile.team_on and \
       not game.results_are_available(request.user.profile.team_on, mode):
        return defaults.page_not_found(request)

    team_to_list_attempts_info = {}
    team_to_score = {}
    team_to_max_best_time = {}
    team_task_to_attempts_info = {}

    task_groups = sorted(game.task_groups.all(), key=lambda tg: tg.number)
    task_group_to_tasks = {}

    for task_group in task_groups:
        task_group_to_tasks[task_group.number] = sorted(task_group.tasks.all(), key=lambda t: t.key_sort())
        for task in task_group_to_tasks[task_group.number]:
            for attempts_info in AttemptsInfo.objects.filter(task=task, mode=mode):
                if attempts_info.best_attempt:
                    team = attempts_info.team

                    if not team.is_hidden:
                        if team not in team_to_score:
                            team_to_score[team] = 0
                        team_to_score[team] += attempts_info.best_attempt.points

                        if attempts_info.best_attempt.points > 0:
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
        max_best_time = team_to_max_best_time[team]
        teams_sorted.append((-score, max_best_time, team))
    teams_sorted = [team for anti_score, max_best_time, team in sorted(teams_sorted)]

    return render(request, 'results.html', {
        'mode': mode,
        'game': game,
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'teams_sorted': teams_sorted,
        'team_to_list_attempts_info': team_to_list_attempts_info,
        'team_to_score': team_to_score,
        'team_to_max_best_time': team_to_max_best_time,
    })


def get_tournament_results(request, game_id):
    return results_page(request, game_id, mode='tournament')
