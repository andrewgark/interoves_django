"""Canonical metadata for games with the daily-game lifecycle.

This registry is the canonical declaration point for daily-game identity,
capabilities, schedules, and shared adapter keys.  Game-specific mechanics
remain in their own modules; shared lifecycle code resolves them through this
registry instead of growing new game-id branches.
"""

from __future__ import annotations

from dataclasses import dataclass

from games.daily.section import (
    ALPHABETTY_SCHEDULE,
    CENSORLY_SCHEDULE,
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
    pager_label: str
    schedule: DailySchedule
    capabilities: DailyGameCapabilities = DailyGameCapabilities()
    aggregate_results_variant: str = 'standard'
    task_results_variant: str = 'standard'
    results_adapter_key: str | None = None
    projection_adapter_key: str | None = None
    completion_adapter_key: str | None = None
    recheck_adapter_key: str | None = None
    board_adapter_key: str | None = None
    share_adapter_key: str | None = None
    statistics_adapter_key: str | None = None

    def __post_init__(self):
        """Reject definitions that advertise unsupported shared features."""
        required_keys = (
            ('statistics', self.capabilities.statistics, self.statistics_adapter_key),
            ('results', self.capabilities.aggregate_results, self.results_adapter_key),
            ('share card', self.capabilities.share_card, self.share_adapter_key),
        )
        for feature, enabled, adapter_key in required_keys:
            if enabled and not adapter_key:
                raise ValueError(
                    'daily game {} enables {} but declares no adapter'.format(
                        self.game_id,
                        feature,
                    )
                )


class DailyGameRegistry:
    """Immutable lookup table for registered daily games."""

    def __init__(self, definitions: tuple[DailyGameDefinition, ...]):
        for definition in definitions:
            if definition.game_id != definition.schedule.game_id:
                raise ValueError(
                    'daily game id must match its schedule game id: {}'.format(
                        definition.game_id,
                    )
                )
        by_id = {definition.game_id: definition for definition in definitions}
        if len(by_id) != len(definitions):
            raise ValueError('daily game ids must be unique')
        task_types = {definition.task_type for definition in definitions}
        if len(task_types) != len(definitions):
            raise ValueError('daily task types must be unique')
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
        pager_label='лесенками',
        schedule=LADDER_SCHEDULE,
        aggregate_results_variant='ladder',
        board_adapter_key='raddle',
        share_adapter_key='ladder',
        statistics_adapter_key='ladder',
        results_adapter_key='ladder',
        projection_adapter_key='attempts_info',
        completion_adapter_key='standard',
        recheck_adapter_key='chain',
    ),
    DailyGameDefinition(
        game_id=ALPHABETTY_SCHEDULE.game_id,
        task_type='alphabetty',
        title='Алфавитка',
        short_title='Алфавитка',
        pager_label='алфавитками',
        schedule=ALPHABETTY_SCHEDULE,
        capabilities=DailyGameCapabilities(share_card=False),
        aggregate_results_variant='alphabetty',
        task_results_variant='alphabetty',
        statistics_adapter_key='alphabet',
        results_adapter_key='alphabetty',
        projection_adapter_key='attempts_info',
        completion_adapter_key='standard',
        recheck_adapter_key='chain',
    ),
    DailyGameDefinition(
        game_id=CENSORLY_SCHEDULE.game_id,
        task_type='censorly',
        title='Цензурка',
        short_title='Цензурка',
        pager_label='цензурками',
        schedule=CENSORLY_SCHEDULE,
        capabilities=DailyGameCapabilities(
            share_card=False,
            statistics=True,
            aggregate_results=True,
        ),
        aggregate_results_variant='alphabetty',
        task_results_variant='alphabetty',
        statistics_adapter_key='censorly',
        results_adapter_key='censorly',
        projection_adapter_key='attempts_info',
        completion_adapter_key='standard',
        recheck_adapter_key='chain',
    ),
    DailyGameDefinition(
        game_id=WORD_SALAD_SCHEDULE.game_id,
        task_type='word_salad',
        title='Салатик',
        short_title='Салатик',
        pager_label='салатиками',
        schedule=WORD_SALAD_SCHEDULE,
        task_results_variant='salad_words',
        board_adapter_key='word_salad',
        share_adapter_key='salad',
        statistics_adapter_key='salad',
        results_adapter_key='salad',
        projection_adapter_key='salad_state',
        completion_adapter_key='standard',
        recheck_adapter_key='word_salad',
    ),
))


def get_daily_game(game_id: str | None) -> DailyGameDefinition | None:
    """Return the registered definition without changing existing callers."""
    return DAILY_GAME_REGISTRY.get(game_id)
