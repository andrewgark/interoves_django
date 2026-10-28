"""Games of a project the current visitor may see, newest first."""

from django.shortcuts import get_object_or_404

from games.identity import has_profile
from games.models import Game, Project

GAMES_PER_PAGE = 20


def project_games_list(request, project_id):
    team = request.user.profile.team_on if has_profile(request.user) else None
    project = get_object_or_404(Project, id=project_id)
    games = [
        game for game in Game.objects.filter(project=project)
        if game.has_access('see_game_preview', team=team)
    ]
    games.sort(key=lambda game: (game.start_time, game.name), reverse=True)
    from games.club.access.archive import user_can_access_desyatka
    for game in games:
        game.is_archive_locked = not user_can_access_desyatka(request.user, game)
    return games
