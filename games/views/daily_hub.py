"""Shared access policy for public daily game hubs and play previews."""

from games.views.util import has_profile


def can_access_daily_game(request, game) -> bool:
    """Use the section game's regular preview permission for daily pages."""
    team = request.user.profile.team_on if has_profile(request.user) else None
    return bool(game.has_access('see_game_preview', team=team))
