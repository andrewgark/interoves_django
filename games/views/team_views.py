from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.shortcuts import get_object_or_404, render
from games.forms import CreateTeamForm, JoinTeamForm
from games.models import ProfileTeamMembership, Team
from games.views.util import redirect_to_referer, has_profile, has_team


def _team_from_request(request, fallback_team):
    tid = (request.GET.get('team') or request.POST.get('team') or '').strip()
    if tid:
        return Team.objects.filter(pk=tid).first()
    return fallback_team


@user_passes_test(has_profile)
def create_team(request):
    user = request.user
    form = CreateTeamForm(project=request.POST.get('project'), data=request.POST)
    if form.is_valid():
        team = form.save()
        user.profile.add_team_membership(team, make_primary=True)
        user.profile.team_requested = None
        user.profile.save(update_fields=['team_requested'])
    return redirect_to_referer(request)


@user_passes_test(has_profile)
def join_team(request):
    user = request.user
    form = JoinTeamForm(project=request.POST.get('project'), data=request.POST)
    if form.is_valid() and form.cleaned_data['name'] and not user.profile.team_requested:
        team = get_object_or_404(Team, name=form.cleaned_data['name'])
        user.profile.team_requested = team
        user.profile.save(update_fields=['team_requested'])
    return redirect_to_referer(request)


@user_passes_test(has_profile)
def quit_from_team(request):
    user = request.user
    profile = user.profile
    if request.method == 'GET':
        ProfileTeamMembership.objects.filter(profile=profile).delete()
        profile.team_on = None
        profile.team_requested = None
        profile.save()
        return redirect_to_referer(request)
    if request.POST.get('cancel_request') == '1':
        profile.team_requested = None
        profile.join_accept_as_primary = True
        profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
        return redirect_to_referer(request)
    team_pk = (request.POST.get('team') or '').strip() or (profile.team_on_id or '')
    if team_pk:
        team = Team.objects.filter(pk=team_pk).first()
        if team and ProfileTeamMembership.objects.filter(profile=profile, team=team).exists():
            profile.remove_team_membership(team)
            if profile.team_requested_id == team.pk:
                profile.team_requested = None
                profile.save(update_fields=['team_requested'])
        return redirect_to_referer(request)
    return redirect_to_referer(request)


@user_passes_test(has_team)
def process_user_request(request, user_id, action):
    active_user = request.user
    passive_user = get_object_or_404(get_user_model(), id=int(user_id))
    if not has_profile(passive_user):
        return redirect_to_referer(request)
    if action == 'kick_out':
        team = _team_from_request(request, active_user.profile.team_on)
        if team is None:
            return redirect_to_referer(request)
        if not ProfileTeamMembership.objects.filter(profile=active_user.profile, team=team).exists():
            return redirect_to_referer(request)
        if not ProfileTeamMembership.objects.filter(profile=passive_user.profile, team=team).exists():
            return redirect_to_referer(request)
        passive_user.profile.remove_team_membership(team)
        return redirect_to_referer(request)
    if active_user == passive_user:
        return redirect_to_referer(request)
    requested = passive_user.profile.team_requested
    if not requested:
        return redirect_to_referer(request)
    if not ProfileTeamMembership.objects.filter(profile=active_user.profile, team=requested).exists():
        return redirect_to_referer(request)
    if action == 'confirm':
        with transaction.atomic():
            mk_primary = passive_user.profile.join_accept_as_primary
            passive_user.profile.team_requested = None
            passive_user.profile.join_accept_as_primary = True
            passive_user.profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
            passive_user.profile.add_team_membership(requested, make_primary=mk_primary)
    elif action == 'reject':
        passive_user.profile.team_requested = None
        passive_user.profile.join_accept_as_primary = True
        passive_user.profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
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
