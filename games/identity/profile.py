"""Authenticated player profile helpers shared by gameplay and HTTP views."""


def has_profile(user):
    return user and getattr(user, 'profile', None)


def has_team(user):
    if not has_profile(user):
        return False
    user.profile.repair_primary_team()
    return user.profile.team_on_id is not None
