"""Shared side effects after a daily logical-game completion."""

from dataclasses import dataclass
from typing import Callable

from games.daily.registry import get_daily_game


@dataclass(frozen=True)
class DailyCompletionAdapter:
    """Registry contract for replay-safe post-completion effects."""

    key: str
    apply: Callable


DAILY_COMPLETION_ADAPTERS = {}


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


DAILY_COMPLETION_ADAPTERS['standard'] = DailyCompletionAdapter(
    'standard', daily_completion_effects,
)


def get_daily_completion_adapter(game_id, *, fallback_key='standard'):
    """Resolve a daily completion adapter with an explicit legacy fallback."""
    definition = get_daily_game(game_id)
    adapter_key = (
        definition.completion_adapter_key
        if definition is not None and definition.completion_adapter_key
        else fallback_key
    )
    return DAILY_COMPLETION_ADAPTERS.get(adapter_key)


def daily_completion_effects_for_game(game_id, completion, **kwargs):
    """Apply the registered completion policy without changing caller contracts."""
    adapter = get_daily_completion_adapter(game_id)
    if adapter is None:
        raise KeyError('unknown daily completion adapter for {}'.format(game_id))
    return adapter.apply(completion, **kwargs)
