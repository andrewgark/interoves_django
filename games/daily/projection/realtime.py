"""Realtime invalidation helpers for custom daily-game pages."""


def publish_actor_state_change(*, task, game, user, replay_slot=None, reason):
    """Notify other authenticated pages to refresh their authoritative state.

    These pages have game-specific state endpoints rather than task-card
    projections. The existing Track event is only an invalidation signal; the
    receiving page must fetch its own state through its authorized endpoint.
    Anonymous and replay actors are deliberately not broadcast.
    """
    if user is None or replay_slot is not None:
        return
    from games.realtime.track_compat import track_actor_task_change

    # Custom daily pages fetch their game-specific state; rendering and sending
    # the ordinary task-card HTML would be wasted work and a larger WS payload.
    track_actor_task_change(
        task,
        user=user,
        game=game,
        update_html={},
        reason=reason,
    )
