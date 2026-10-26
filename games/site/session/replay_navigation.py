"""Navigation and viewer resolution for replay endpoints."""

from django.shortcuts import get_object_or_404
from django.http import Http404

from games.analytics_identity import gameplay_anon_key
from games.models import Game, GameTaskGroup, Project
from games.play_mode import get_play_mode
from games.project_navigation import project_base
from games.tasks.navigation import play_url_for_task_group
from games.site.views.pages.util import effective_play_mode, has_profile, has_team


def replay_actor_for_request(
    request,
    game,
    *,
    sections_project_id='sections',
    play_mode_getter=None,
    has_profile_checker=None,
    has_team_checker=None,
    anon_key_getter=None,
):
    """Resolve the authenticated/team/personal actor allowed to replay."""
    if play_mode_getter is None:
        play_mode, _ = get_play_mode(
            request,
            game.project_id,
            sections_project_id=sections_project_id,
        )
    else:
        play_mode, _ = play_mode_getter(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    has_profile_checker = has_profile_checker or has_profile
    has_team_checker = has_team_checker or has_team
    anon_key_getter = anon_key_getter or gameplay_anon_key
    team = user = anon_key = None
    if play_mode == 'team':
        if not request.user.is_authenticated or not has_team_checker(request.user):
            raise Http404()
        team = request.user.profile.team_on
    elif request.user.is_authenticated:
        if not has_profile_checker(request.user):
            raise Http404()
        user = request.user
    else:
        anon_key = anon_key_getter(request)
        if not anon_key:
            raise Http404()
    return team, user, anon_key


def replay_game_and_placement(game_id, task_group_number, project_id=None, *, user=None):
    """Load the game and its task-group placement for a replay endpoint."""
    if project_id:
        project = get_object_or_404(Project, id=project_id)
        game = get_object_or_404(Game, id=game_id, project=project)
    else:
        game = get_object_or_404(Game, id=game_id)
    from games.daily.page.placement import resolve_accessible_daily_placement
    from games.daily_section import is_scheduled_game
    from games.placement_share import is_share_hash_segment

    if is_scheduled_game(game.id) or is_share_hash_segment(str(task_group_number)):
        placement = resolve_accessible_daily_placement(
            game, task_group_number, user=user,
        )
        if placement is None:
            raise Http404()
    else:
        placement = get_object_or_404(
            GameTaskGroup.objects.select_related('task_group'),
            game=game,
            number=str(task_group_number),
        )
    return game, placement


def redirect_after_replay_action(game, number, project_id=None):
    """Return the canonical task-group URL after starting/exiting replay."""
    return play_url_for_task_group(
        game,
        number,
        project_base=project_base(project_id) if project_id else '',
    )
