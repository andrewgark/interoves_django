"""Compatibility facade for daily streak helpers."""

from games.daily.streak import (
    daily_streaks_for_actor,
    daily_streaks_for_user,
    streak_from_completion_dates,
)

__all__ = [
    'daily_streaks_for_actor',
    'daily_streaks_for_user',
    'streak_from_completion_dates',
]
