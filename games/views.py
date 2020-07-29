import json
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
        if game.has_access('see_game_preview', team=team):
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
            task_to_attempts_info[task.id] = Attempt.manager.get_attempts_info(team=team, task=task, mode=mode)
    return task_to_attempts_info


def game_page(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    if not has_profile(request.user):
        raise UserHasNoProfileException('User {} has no profile'.format(request.user))
    if not request.user.profile.team_on:
        return PlayGameWithoutTeamException('User {} tries to sent attempt but has no team'.format(request.user.profile))
    if not game.has_access('play_with_team', team=request.user.profile.team_on):
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


def process_send_attempt(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    if not has_profile(request.user):
        raise UserHasNoProfileException('User {} has no profile'.format(request.user))
    if not request.user.profile.team_on:
        return PlayGameWithoutTeamException('User {} tries to sent attempt but has no team'.format(request.user.profile))

    team = request.user.profile.team_on
    game = task.task_group.game

    if not task.task_group.game.has_access('play_with_team', team=team):
        return NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    if task.task_type == 'default':
        form = AttemptForm(request.POST)
        if not form.is_valid():
            return InvalidFormException('attempt form {} is not valid'.format(form))

        attempt = form.save(commit=False)
    elif task.task_type == 'wall':
        if 'text' not in request.POST:            
            request_data = {
                'words': sorted(request.POST.getlist('words[]')),
                'stage': request.POST['stage'],
            }
        else:
            request_data = {
                'explanation': request.POST['text'],
                'words': json.loads(request.POST['words']),
                'stage': request.POST['stage'],
            }
        attempt = Attempt(text=json.dumps(request_data))
    else:
        raise Exception('Unknown task_type: {}'.format(task.task_type))
    attempt.team = team
    attempt.task = task
    attempt.time = timezone.now()

    current_mode = game.get_current_mode(attempt)
    modes = ['general']
    if current_mode == 'tournament':
        modes.append('tournament')

    last_attempt_state = None
    for mode in modes:
        attempts = Attempt.manager.get_attempts(team, task, mode)
        if mode == 'general' and attempts:
            last_attempt_state = attempts[-1].state

        if mode == 'tournament':
            if task.task_type == 'wall':
                validation_data = task.get_wall().validate_max_attempts(attempts, attempt)
                if validation_data is not None:
                    stage, n_attempts, max_attempts = validation_data
                    raise TooManyAttemptsException('Team {} exceeds attempts limit ({}) in wall task {} on stage {}'.format(team, max_attempts, task, stage))
            else:
                n_attempts = len(attempts)
                max_attempts = task.get_max_attempts()
                if n_attempts >= max_attempts:
                    raise TooManyAttemptsException('Team {} exceeds attempts limit ({}) in task {}'.format(team, max_attempts, task))

        for other_attempt in attempts:
            if attempt.text == other_attempt.text:
                raise DuplicateAttemptException('Attempt duplicates one of the previous attempts by this team')

    checker = CheckerFactory().create_checker(task.get_checker(), task.checker_data, last_attempt_state)
    check_result = checker.check(attempt.text)
    attempt.status, attempt.points, attempt.state = check_result.status, check_result.points, check_result.state
    if 'tournament' in modes and attempt.status != 'Ok':
        attempt.possible_status = attempt.status
        attempt.status = check_result.tournament_status
    attempt.points *= task.get_points()

    attempt.save()

    return {
        'status': 'ok',
        'task_id': task.id,
        'html': render(request, 'task.html', {
            'task': task,
            'task_group': task.task_group,
            'attempts_info': Attempt.manager.get_attempts_info(team=team, task=task, mode=current_mode),
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


def task_ok_by_team(task, team, mode):
    best_attempt = Attempt.manager.get_attempts_info(team=team, task=task, mode=mode).best_attempt
    return best_attempt and best_attempt.status == 'Ok'


def get_answer(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    if not has_profile(request.user):
        raise UserHasNoProfileException('User {} has no profile'.format(request.user))
    if not request.user.profile.team_on:
        return PlayGameWithoutTeamException('User {} tries to sent attempt but has no team'.format(request.user.profile))

    team = request.user.profile.team_on
    game = task.task_group.game

    if not task.task_group.game.has_access('play_with_team', team=team):
        return NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    mode = game.get_current_mode()

    if mode != 'general' and not task_ok_by_team(task, request.user.profile.team_on, mode):
        return NoAnswerAccessException('User {} has no access to answers to task {} right now'.format(request.user.profile, task))

    return JsonResponse({
        'html': render(request, 'answer.html', {
            'task': task,
        }).content.decode('UTF-8'),
    })


def results_page(request, game_id, mode='general'):
    game = get_object_or_404(Game, id=game_id)
    if has_profile(request.user) and request.user.profile.team_on and \
       not game.has_access('see_results', mode=mode):
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
            for attempts_info in Attempt.manager.get_task_attempts_infos(task=task, mode=mode):
                if attempts_info.best_attempt:
                    team = attempts_info.best_attempt.team

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
        max_best_time = team_to_max_best_time.get(team, None)
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
