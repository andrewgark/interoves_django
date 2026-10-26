"""Main UI: hub, games folder, profile, team."""
import logging

from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from games.identity import has_profile, has_team
from games.site.pages import (
    NEW_UI_SECTIONS_PROJECT,
    _anon_key_from_request,
    _get_play_mode,
)
from games.site.session.replay import clear_replay_session, start_or_reset_replay
from games.site.session.replay_navigation import (
    redirect_after_replay_action,
    replay_actor_for_request,
    replay_game_and_placement,
)

logger = logging.getLogger('games.views.new_ui')

def _replay_actor_for_request(request, game):
    """Compatibility wrapper for replay actor resolution."""
    return replay_actor_for_request(
        request,
        game,
        sections_project_id=NEW_UI_SECTIONS_PROJECT,
        play_mode_getter=_get_play_mode,
        has_profile_checker=has_profile,
        has_team_checker=has_team,
        anon_key_getter=_anon_key_from_request,
    )


def _replay_game_and_placement(game_id, task_group_number, project_id=None, user=None):
    return replay_game_and_placement(game_id, task_group_number, project_id, user=user)


def _redirect_after_replay_action(game, number, project_id=None):
    return redirect_after_replay_action(game, number, project_id)


@require_POST
def replay_start(request, game_id, task_group_number, project_id=None):
    """Explicitly start/reset the private replay slot for one task group."""
    game, placement = _replay_game_and_placement(
        game_id, task_group_number, project_id, user=request.user,
    )
    from games.club.access.archive import reject_if_club_archive_blocked
    locked = reject_if_club_archive_blocked(request, game, number=placement.number)
    if locked is not None:
        return locked
    team, user, anon_key = _replay_actor_for_request(request, game)
    start_or_reset_replay(
        request=request,
        game=game,
        task_group=placement.task_group,
        team=team,
        user=user,
        anon_key=anon_key,
    )
    return redirect(_redirect_after_replay_action(game, task_group_number, project_id))


@require_POST
def replay_exit(request, game_id, task_group_number, project_id=None):
    """Leave the private replay and show the preserved official solution."""
    game, placement = _replay_game_and_placement(
        game_id, task_group_number, project_id, user=request.user,
    )
    team, user, anon_key = _replay_actor_for_request(request, game)
    clear_replay_session(request, game, placement.task_group)
    return redirect(_redirect_after_replay_action(game, task_group_number, project_id))


__all__ = [
    '_replay_actor_for_request',
    '_replay_game_and_placement',
    '_redirect_after_replay_action',
    'new_replay_start',
    'new_replay_exit',
]

# Names kept for existing imports and patches.
new_replay_exit = replay_exit
new_replay_start = replay_start
