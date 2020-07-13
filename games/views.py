from django.contrib.auth import get_user_model
from django.shortcuts import render 
from games.models import Team, Game
from games.forms import CreateTeamForm, JoinTeamForm
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
    if passive_user and passive_user[0] and passive_user[0].profile:
        passive_user = passive_user[0]
        if has_profile(active_user) and has_profile(passive_user) and \
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

def game_page(request, game_id):
    # добавить сюда обработку ошибки, если такой игры нет
    game = Game.objects.filter(id=game_id)
    if game and game[0]:
        game = game[0]
        task_groups = sorted(game.task_groups.all(), key=lambda tg: tg.number)
        return render(request, 'game.html', {
            'game': game,
            'task_groups': task_groups,
        })
    return main_page(request)
