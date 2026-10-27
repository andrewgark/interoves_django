"""Who may press «Сбросить решение» on a custom offer, and whose run it clears."""

from __future__ import annotations

from games.ops.analytics.identity import gameplay_anon_key


def offer_reset_actors(request, offer):
    """Return actor kwargs to reset for the requester, or None if forbidden.

    Always the requester's own progress: staff opening someone's offer expect
    their own run to reset, not the author's. Accepted offers have official
    stats and a replay button for players, so only the author and staff reset
    there; an unaccepted draft has neither, so anyone (anonymous included)
    may reset their own run.
    """
    user = request.user
    if not user.is_authenticated:
        anon_key = None if offer.accepted_link_id else gameplay_anon_key(request)
        return [{'anon_key': anon_key}] if anon_key else None
    if offer.accepted_link_id and offer.user_id != user.id and not user.is_staff:
        return None
    # Both scopes regardless of the current browser mode, otherwise switching
    # modes can resurrect the old solution.
    actors = [{'user': user}]
    team = getattr(getattr(user, 'profile', None), 'team_on', None)
    if team is not None:
        actors.append({'team': team})
    return actors
