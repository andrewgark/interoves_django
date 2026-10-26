"""Hub views: resolve team from authenticated user."""

from games.identity import has_profile


def team_for_user(user):
    """Return active team for gameplay hub cards, or None for anonymous users."""
    if has_profile(user):
        return user.profile.team_on
    return None
