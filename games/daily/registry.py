"""Canonical metadata for games with the daily-game lifecycle.

This registry is intentionally metadata-only for now.  It gives new daily
games one place to declare their identity and supported shared features before
we start routing existing views through adapters.
"""

from __future__ import annotations

from dataclasses import dataclass

from games.daily.section import (
    ALPHABETTY_SCHEDULE,
    LADDER_SCHEDULE,
    WORD_SALAD_SCHEDULE,
    DailySchedule,
)


@dataclass(frozen=True)
class DailyGameCapabilities:
    """Shared lifecycle features available to a daily game."""

    timing: bool = True
    archive: bool = True
    statistics: bool = True
    share_card: bool = True
    aggregate_results: bool = True


@dataclass(frozen=True)
class DailyGameDefinition:
    """Stable identity and shared capabilities of one daily game."""

    game_id: str
    task_type: str
    title: str
    short_title: str
    schedule: DailySchedule
    capabilities: DailyGameCapabilities = DailyGameCapabilities()


class DailyGameRegistry:
    """Immutable lookup table for registered daily games."""

    def __init__(self, definitions: tuple[DailyGameDefinition, ...]):
        by_id = {definition.game_id: definition for definition in definitions}
        if len(by_id) != len(definitions):
            raise ValueError('daily game ids must be unique')
        self._definitions = tuple(definitions)
        self._by_id = by_id

    def all(self) -> tuple[DailyGameDefinition, ...]:
        return self._definitions

    def get(self, game_id: str | None) -> DailyGameDefinition | None:
        if not game_id:
            return None
        return self._by_id.get(str(game_id))

    def require(self, game_id: str) -> DailyGameDefinition:
        definition = self.get(game_id)
        if definition is None:
            raise KeyError('unknown daily game: {}'.format(game_id))
        return definition


DAILY_GAME_REGISTRY = DailyGameRegistry((
    DailyGameDefinition(
        game_id=LADDER_SCHEDULE.game_id,
        task_type='raddle',
        title='Лесенка',
        short_title='Лесенка',
        schedule=LADDER_SCHEDULE,
    ),
    DailyGameDefinition(
        game_id=ALPHABETTY_SCHEDULE.game_id,
        task_type='alphabetty',
        title='Алфавитка',
        short_title='Алфавитка',
        schedule=ALPHABETTY_SCHEDULE,
    ),
    DailyGameDefinition(
        game_id=WORD_SALAD_SCHEDULE.game_id,
        task_type='word_salad',
        title='Салатик',
        short_title='Салатик',
        schedule=WORD_SALAD_SCHEDULE,
    ),
))


def get_daily_game(game_id: str | None) -> DailyGameDefinition | None:
    """Return the registered definition without changing existing callers."""
    return DAILY_GAME_REGISTRY.get(game_id)
