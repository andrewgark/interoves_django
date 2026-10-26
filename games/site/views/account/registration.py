from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST
from games.models import Registration, Game, Team
from games.site.views.pages.util import has_team, redirect_to_referer


@user_passes_test(has_team)
@require_POST
def register_to_game(request, game_id, project_id=None):
    with_referent = request.POST.get('with_referent') or request.GET.get('with_referent') or None
    team = request.user.profile.team_on
    games = Game.objects.filter(id=game_id)
    if project_id:
        games = games.filter(project_id=project_id)
    game = get_object_or_404(games)
    # Serialize registrations by teammates and commit the ticket with the entry.
    with transaction.atomic():
        team = Team.objects.select_for_update().get(pk=team.pk)
        if game.has_registered(team):
            messages.info(request, 'Ваша команда уже зарегистрирована на эту игру')
            return redirect_to_referer(request)
        if not game.has_access('register', team=team):
            messages.error(request, 'В эту игру нельзя зарегистрироваться')
            return redirect_to_referer(request)
        referent = get_object_or_404(Team, name=with_referent) if with_referent is not None else None
        if game.requires_ticket:
            if referent is not None:
                if not Registration.objects.filter(game=game, team=referent).exists():
                    messages.error(request, 'Команда-референт {} не зарегистрирована на игру'.format(str(referent)))
                    return redirect_to_referer(request)
                referent_registrations = Registration.objects.filter(team=team, with_referent=referent)
                if referent_registrations.count() >= 3:
                    messages.error(request, 'Нельзя зарегистрироваться через команду-референт {} больше чем 3 раза'.format(str(referent)))
                    return redirect_to_referer(request)
            else:
                if team.tickets < 1:
                    messages.error(request, 'Чтобы зарегистрироваться на игру, команда должна купить билет')
                    return redirect_to_referer(request)
                team.tickets -= 1
                team.save(update_fields=['tickets'])
        Registration.objects.create(team=team, game=game, with_referent=referent)
    return redirect_to_referer(request)
