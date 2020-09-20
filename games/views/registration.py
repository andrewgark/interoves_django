from django.http import JsonResponse
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import get_object_or_404
from games.exception import *
from games.models import Registration, Game
from games.views.util import has_team, redirect_to_referer


@user_passes_test(has_team)
def register_to_game(request, game_id):
    team = request.user.profile.team_on
    game = get_object_or_404(Game, id=game_id)
    if not game.has_access('register', team=team):
        raise CantRegisterException('В эту игру нельзя зарегистрироваться')
    if team.tickets < 1:
        raise NoTicketsException('Чтобы зарегистрироваться на игру, команда должна купить билет')
    reg = Registration(team=team, game=game)
    reg.save()
    team.tickets = team.tickets - 1
    team.save()
    return redirect_to_referer(request)
