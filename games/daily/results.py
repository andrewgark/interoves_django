"""Registry-backed contracts for daily results presentation variants."""

from dataclasses import dataclass

from games.daily.registry import get_daily_game


@dataclass(frozen=True)
class DailyResultsAdapter:
    """Presentation variants used by aggregate and task results pages."""

    key: str
    aggregate_variant: str
    task_variant: str


DAILY_RESULTS_ADAPTERS = {
    'ladder': DailyResultsAdapter('ladder', 'ladder', 'standard'),
    'alphabetty': DailyResultsAdapter('alphabetty', 'alphabetty', 'alphabetty'),
    'censorly': DailyResultsAdapter('censorly', 'alphabetty', 'alphabetty'),
    'salad': DailyResultsAdapter('salad', 'standard', 'salad_words'),
}


def get_daily_results_adapter(game_id):
    """Return the results presentation contract for a registered daily game."""
    definition = get_daily_game(game_id)
    if definition is None or not definition.results_adapter_key:
        return None
    return DAILY_RESULTS_ADAPTERS.get(definition.results_adapter_key)
