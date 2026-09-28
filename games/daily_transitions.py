"""Compatibility facade for daily content transition helpers."""

from games.daily.transitions import (
    next_daily_content_transition,
    next_daily_content_transition_for_game,
    next_daily_content_transition_for_games,
)

__all__ = [
    'next_daily_content_transition',
    'next_daily_content_transition_for_game',
    'next_daily_content_transition_for_games',
]
