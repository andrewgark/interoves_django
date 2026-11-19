"""Authenticated player profile helpers shared by gameplay and HTTP views."""


def has_profile(user):
    return user and getattr(user, 'profile', None)


def has_team(user):
    if not has_profile(user):
        return False
    profile = user.profile
    # The hub asks once per card (8 membership queries per view). Repair once
    # per loaded profile; Profile.save() clears the mark.
    if not getattr(profile, '_primary_team_repaired', False):
        profile.repair_primary_team()
        profile._primary_team_repaired = True
    return profile.team_on_id is not None
