"""Dispatch registry for game-specific daily share-card adapters."""

from dataclasses import dataclass
from typing import Callable

from games.daily.registry import get_daily_game
from games.daily.share_card import attach_ladder_share_card, attach_salad_share_card


@dataclass(frozen=True)
class DailyShareAdapter:
    """Attach one game's structured share-card payload to a UI payload."""

    key: str
    attach: Callable


DAILY_SHARE_ADAPTERS = {
    adapter.key: adapter
    for adapter in (
        DailyShareAdapter('ladder', attach_ladder_share_card),
        DailyShareAdapter('salad', attach_salad_share_card),
    )
}


def get_daily_share_adapter(game_id, *, fallback_key=None):
    """Resolve a share adapter from registry metadata or an explicit legacy fallback."""
    definition = get_daily_game(game_id)
    adapter_key = (
        definition.share_adapter_key
        if definition is not None and definition.share_adapter_key
        else fallback_key
    )
    if not adapter_key:
        return None
    return DAILY_SHARE_ADAPTERS.get(adapter_key)
