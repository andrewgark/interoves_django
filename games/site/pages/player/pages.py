"""Public player page: who this is, their teams and daily-game stats."""

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from games.community import friends, visibility
from games.community.stats import head_to_head, player_stats
from games.identity import has_profile
from games.models import Profile, Team
from games.models.people import PersonalResultsParticipant
from games.results.table.context import club_subscriber_user_ids
from games.site.pages import NEW_UI_PROJECT, _project_urls_context


@require_http_methods(['GET'])
def player_profile(request, public_id):
    profile = (
        Profile.objects.select_related('user')
        .filter(public_id=public_id).first()
    )
    state = visibility.page_state(request.user, profile)
    if state == visibility.PAGE_MISSING:
        raise Http404()
    viewer = request.user
    is_owner = visibility.is_owner(viewer, profile)
    identity = visibility.public_identity(
        profile, viewer, page_visible=state == visibility.PAGE_FULL,
    )
    ctx = {
        'player': identity,
        'player_public_id': profile.public_id,
        # Friend actions need the viewer's own profile; without one they 404.
        'player_relation': friends.relation(viewer, profile.user_id) if has_profile(viewer) else '',
        'player_is_owner': is_owner,
        'player_page_hidden': state == visibility.PAGE_HIDDEN,
        'player_owner_page_hidden': is_owner and profile.page_hidden,
        'player_owner_avatar_hidden': is_owner and profile.avatar_hidden and bool(profile.avatar_url),
        'page_title': identity.name,
        'robots_noindex': True,
    }
    if state == visibility.PAGE_FULL:
        ctx.update({
            'player_since': profile.user.date_joined,
            'player_is_club': profile.user_id in club_subscriber_user_ids(
                [PersonalResultsParticipant(user_id=profile.user_id)],
            ),
            'player_teams': list(
                Team.objects.filter(member_links__profile=profile, is_hidden=False)
                .order_by('visible_name', 'name').distinct()
            ),
            'player_stats': player_stats(profile.user),
        })
        their_friends = friends.friend_user_ids(profile.user_id)
        ctx['player_friends_count'] = len(their_friends)
        if getattr(viewer, 'is_authenticated', False) and not is_owner:
            ctx['player_mutual_friends_count'] = len(their_friends & friends.friend_user_ids(viewer.pk))
        if getattr(viewer, 'is_authenticated', False) and not is_owner:
            ctx['player_head_to_head'] = head_to_head(viewer, profile.user)
    ctx.update(_project_urls_context(NEW_UI_PROJECT))
    return render(request, 'site/player_profile.html', ctx)
