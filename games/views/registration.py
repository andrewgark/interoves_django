from django.http import JsonResponse
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST
from games.exception import CantRegisterException, NoTicketsException
from games.models import Registration, Game, Team
from games.views.util import has_team, redirect_to_referer


@user_passes_test(has_team)
@require_POST
def register_to_game(request, game_id, project_id=None):
    with_referent = request.POST.get('with_referent') or request.GET.get('with_referent')
    team = request.user.profile.team_on
    games = Game.objects.filter(id=game_id)
    if project_id:
        games = games.filter(project_id=project_id)
    game = get_object_or_404(games)
    referent = get_object_or_404(Team, name=with_referent) if with_referent is not None else None
    if not game.has_access('register', team=team):
        raise CantRegisterException('В эту игру нельзя зарегистрироваться')
    if game.requires_ticket:
        if with_referent is not None:
            try:
                referent_registration = get_object_or_404(Registration, game=game, team=referent)
            except:
                raise NoTicketsException('Команда-референт {} не зарегистрирована на игру'.format(str(referent)))
            referent_registrations = Registration.objects.filter(team=team, with_referent=referent)
            if len(referent_registrations) >= 3:
                raise NoTicketsException('Нельзя зарегистрироваться через команду-референт {} больше чем 3 раза'.format(str(referent)))
        else:
            if team.tickets < 1:
                raise NoTicketsException('Чтобы зарегистрироваться на игру, команда должна купить билет')
            team.tickets = team.tickets - 1
            team.save()
    reg = Registration(team=team, game=game, with_referent=referent)
    reg.save()
    return redirect_to_referer(request)
