"""Public team page (``/t/<public_id>/``): roster, daily stats, actions."""

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from games.community import visibility
from games.community.stats import team_stats
from games.identity import has_profile
from games.models import Profile, ProfileTeamMembership, Team, TeamInvite
from games.site.pages import NEW_UI_PROJECT, _project_urls_context, _team_invite_url


def _roster(team, viewer):
    """Members as other players see them; admin-hidden profiles are left out.

    A hidden page keeps the name (it is public in results anyway) but drops
    the photo and the link.
    """
    profiles = (
        Profile.objects.filter(team_memberships__team=team, is_hidden=False)
        .order_by('first_name', 'last_name', 'pk').distinct()
    )
    members = []
    for profile in profiles:
        identity = visibility.public_identity(profile, viewer)
        members.append({
            'identity': identity,
            'url': '' if visibility.page_state(viewer, profile) != visibility.PAGE_FULL else identity.url,
            'is_active_team': profile.team_on_id == team.pk,
            'is_viewer': visibility.is_owner(viewer, profile),
        })
    return members


@require_http_methods(['GET'])
def team_public_page(request, public_id):
    team = Team.objects.filter(public_id=public_id).first()
    if team is None:
        raise Http404()
    viewer = request.user
    profile = viewer.profile if has_profile(viewer) else None
    is_member = bool(profile and ProfileTeamMembership.objects.filter(profile=profile, team=team).exists())
    if team.is_hidden and not is_member:
        raise Http404()

    ctx = {
        'team': team,
        'team_url': request.path,
        'team_members': _roster(team, viewer),
        'team_stats': team_stats(team),
        'team_is_member': is_member,
        'team_is_active': bool(is_member and profile.team_on_id == team.pk),
        'page_title': team.visible_name or team.name,
        'robots_noindex': True,
    }
    if is_member:
        ctx['team_join_requests'] = [
            {'user_id': requester.user_id, 'identity': visibility.public_identity(requester, viewer)}
            for requester in team.users_requested.filter(is_hidden=False).order_by('pk')
            if requester.user_id != viewer.pk
        ]
        invite = (
            TeamInvite.objects.filter(team=team, revoked_at__isnull=True)
            .order_by('-created_at').first()
        )
        if invite:
            ctx['team_invite_url'] = _team_invite_url(
                request, invite.token,
                team.project_id if team.project_id != NEW_UI_PROJECT else None,
            )
    elif profile is not None:
        ctx['team_requested'] = profile.team_requested
    ctx.update(_project_urls_context(NEW_UI_PROJECT))
    return render(request, 'site/team_public.html', ctx)
