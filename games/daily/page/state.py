"""Shared state-resolution primitives for daily game adapters."""


def latest_daily_state(
    task,
    game,
    attempts_info,
    *,
    default_state,
    decode_state,
    resolve_chain_state,
    chain_state_kwargs,
):
    """Prefer chain state, then the latest attempt state, then a default."""
    if game is not None:
        current = resolve_chain_state(
            task,
            game,
            **chain_state_kwargs,
        )
        if current and current.state:
            return decode_state(current.state)
    for attempt in reversed(
        (getattr(attempts_info, 'attempts', None) or ())
    ):
        if attempt.state:
            return decode_state(attempt.state)
    return default_state
