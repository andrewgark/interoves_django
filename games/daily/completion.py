"""Shared side effects after a daily logical-game completion."""


def daily_completion_effects(
    completion,
    *,
    replay_slot,
    publish_analytics,
    analytics_kwargs,
):
    """Return timing and analytics events with one replay-safe policy."""
    if completion is None:
        return {'timing': None, 'analytics_events': []}
    analytics_events = []
    if replay_slot is None:
        analytics_events = publish_analytics(**analytics_kwargs)
    return {
        'timing': completion.get('timing'),
        'analytics_events': analytics_events,
    }
