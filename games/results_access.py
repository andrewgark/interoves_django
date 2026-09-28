"""Viewer-specific access and participant helpers for results pages."""

from games.analytics_identity import gameplay_anon_key
from games.play_mode import get_play_mode
from games.results_context import results_me_participants as build_me_participants
from games.views.util import effective_play_mode, has_profile


def anon_key_from_request(request):
    """Return the canonical anonymous browser identity for a results viewer."""
    if request.user.is_authenticated:
        return None
    return gameplay_anon_key(request)


def results_me_participants(request, play_mode):
    """Return the personal or anonymous participant represented by the request."""
    return build_me_participants(
        request,
        play_mode,
        anon_key_from_request=anon_key_from_request,
    )


def results_actor_for_request(request, game, *, sections_project_id='sections'):
    """Return the actor row this viewer would occupy in the current play mode."""
    team = request.user.profile.team_on if has_profile(request.user) else None
    play_mode, _ = get_play_mode(
        request,
        game.project_id,
        sections_project_id=sections_project_id,
    )
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    me_personal, me_anon_participant = results_me_participants(request, play_mode)
    if play_mode == 'team':
        return team
    return me_personal or me_anon_participant


def public_exclusion_notice(request, game, task_group, *, surface, sections_project_id='sections'):
    """Build the explanation shown when a viewer is excluded from public results."""
    from games.leaderboard import results_exclusion_notice, viewer_results_exclusion_reasons

    user = request.user if getattr(request.user, 'is_authenticated', False) else None
    reasons = viewer_results_exclusion_reasons(
        results_actor_for_request(
            request,
            game,
            sections_project_id=sections_project_id,
        ),
        task_group=task_group,
        user=user,
    )
    return results_exclusion_notice(reasons, surface=surface)
