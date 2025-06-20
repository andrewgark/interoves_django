from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import get_object_or_404, render
from games.forms import CreateTeamForm, JoinTeamForm
from games.models import Team
from games.views.util import redirect_to_referer, has_profile, has_team


@user_passes_test(has_profile)
def create_team(request):
    user = request.user
    form = CreateTeamForm(project=request.POST.get('project'), data=request.POST)
    if form.is_valid() and not user.profile.team_on:
        team = form.save()
        user.profile.team_on = team
        user.profile.team_requested = None
        user.profile.save()
    return redirect_to_referer(request)


@user_passes_test(has_profile)
def join_team(request):
    user = request.user
    form = JoinTeamForm(project=request.POST.get('project'), data=request.POST)
    if form.is_valid() and form.cleaned_data['name'] and \
       not user.profile.team_on and not user.profile.team_requested:
        team = get_object_or_404(Team, name=form.cleaned_data['name'])
        user.profile.team_requested = team
        user.profile.save()
    return redirect_to_referer(request)


@user_passes_test(has_profile)
def quit_from_team(request):
    user = request.user
    user.profile.team_on = None
    user.profile.team_requested = None
    user.profile.save()
    return redirect_to_referer(request)


@user_passes_test(has_team)
def process_user_request(request, user_id, action):
    active_user = request.user
    passive_user = get_object_or_404(get_user_model(), id=int(user_id))
    if has_profile(passive_user):
        if action == 'kick_out':
            if active_user.profile.team_on == passive_user.profile.team_on:
                passive_user.profile.team_on = None
                passive_user.profile.save()
        else:
            if active_user != passive_user and \
               active_user.profile.team_on == passive_user.profile.team_requested:
                passive_user.profile.team_requested = None
                if action == 'confirm':
                    passive_user.profile.team_on = active_user.profile.team_on
                elif action == 'reject':
                    passive_user.profile.team_on = None
                passive_user.profile.save()
    return redirect_to_referer(request)


def confirm_user_joining_team(request, user_id):
    return process_user_request(request, user_id, 'confirm')


def reject_user_joining_team(request, user_id):
    return process_user_request(request, user_id, 'reject')


def kick_out_user(request, user_id):
    return process_user_request(request, user_id, 'kick_out')


def get_team_to_play_page(request, game):
    from games.forms import CreateTeamForm, JoinTeamForm
    return render(request, 'get_team_to_play.html', {
        'game': game,
        'create_team_form': CreateTeamForm(game.project),
        'join_team_form': JoinTeamForm(game.project),
        'project': game.project
    })
