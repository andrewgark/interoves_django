from django.contrib.auth import get_user_model
from django.shortcuts import render 
from django.template.defaulttags import register
from games.check import CheckerFactory
from games.forms import CreateTeamForm, JoinTeamForm, AttemptForm
from games.models import Team, Game, Attempt, AttemptsInfo
from datetime import datetime

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
        'today': datetime.now()
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
        team = Team.objects.filter(name=form.cleaned_data['name'])
        if team and team[0]:
            user.profile.team_requested = team[0]
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
    passive_user = get_user_model().objects.filter(id=int(user_id))
    if passive_user and has_profile(passive_user[0]):
        passive_user = passive_user[0]
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


def get_task_to_attempts_info(game, team):
    task_to_attempts_info = {}
    for task_group in game.task_groups.all():
        for task in task_group.tasks.all():
            attempts_info = AttemptsInfo.objects.filter(team=team)
            if attempts_info and attempts_info[0]:
                task_to_attempts_info[task.id] = attempts_info[0]
            else:
                task_to_attempts_info[task.id] = None
    return task_to_attempts_info


def game_page(request, game_id):
    # добавить сюда обработку ошибки, если такой игры нет
    game = Game.objects.filter(id=game_id)
    if has_profile(request.user) and request.user.profile.team_on and game and game[0]:
        game = game[0]
        task_to_attempts_info = get_task_to_attempts_info(game, request.user.profile.team_on)
        task_groups = sorted(game.task_groups.all(), key=lambda tg: tg.number)
        task_group_to_tasks = {}
        for task_group in task_groups:
            task_group_to_tasks[task_group.number] = sorted(task_group.tasks.all(), key=lambda t: t.number)
        return render(request, 'game.html', {
            'game': game,
            'task_groups': task_groups,
            'task_group_to_tasks': task_group_to_tasks,
            'task_to_attempts_info': task_to_attempts_info,
        })
    return main_page(request)


def inherite_task_properties(task):
    task_group = task
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


def update_attempts_info(attempts_info, attempt):
    attempts_info.best_attempt = attempt
    list_attempts = sorted(attempt.attempts_info.attempts, lambda x: x.best_time)  
    for attempt in list_attempts:
        if attempt.points > best_attempt.points or \
            (attempt.points == best_attempt.points and better_status(attempt.status, best_attempt.status)):
            attempts_info.best_attempt = attempt
    attempts_info.save()


def send_attempt(request, task_id):
    task = Task.objects.filter(id=task_id)
    if task and task[0] and has_profile(request.user) and request.user.profile.team_on:
        task = task[0]
        team = request.user.profile.team_on
        form = AttemptForm(request.POST)
        if form.is_valid():
            attempt = form.save(commit=False)
            attempt.team = team
            attempt.task = task
            attempt.time = datetime.now()
            checker_type, points, max_attempts = inherite_task_properties(task)
            
            attempts_info = AttemptsInfo.objects.filter(team=team, task=task)
            n_attempts = 0
            if attempts_info and attempts_info[0]:
                attempt.attempts_info = attempts_info[0]
                n_attempts = len(attempt.attempts_info.attempts)
            else:
                attempt.attempts_info = AttemptsInfo(task=task, team=team)

            if n_attempts >= max_attempts:
                # отправлять ошибку, что слишком много посылок
                return game_page(request)

            checker = CheckerFactory().create_checker(checker_type, attempt.task.checker_data)
            attempt.result, attempt.points = checker.check(attempt.text)
            attempt.points *= points

            update_attempts_info(attempt.attempts_info, attempt)
            
            attempt.save()
    return game_page(request)


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)
