"""Game-specific board payload builders used by the shared daily view."""

from dataclasses import dataclass
from typing import Callable

from games.daily.registry import get_daily_game


def _word_salad_puzzle():
    from games.puzzles.salad.puzzle import WordSaladPuzzle

    return WordSaladPuzzle


def _raddle_puzzle():
    from games.puzzles.raddle.puzzle import RaddlePuzzle

    return RaddlePuzzle


@dataclass(frozen=True)
class DailyBoardAdapter:
    """Dispatch entry for one task type's board payload builder."""

    task_type: str
    build: Callable
    prepare: Callable
    context_key: str
    body_template: str | None = None
    body_wrapper: bool | None = None
    show_attempts: bool | None = None
    show_answer: bool | None = None


def build_word_salad_board_data(**kwargs):
    """Compatibility facade. The salad board lives on ``WordSaladPuzzle``."""
    return _word_salad_puzzle().build_board(**kwargs)


def prepare_word_salad_board_data(**kwargs):
    """Compatibility facade. The salad board lives on ``WordSaladPuzzle``."""
    return _word_salad_puzzle().prepare_board(**kwargs)


def build_raddle_board_data(**kwargs):
    """Compatibility facade. The ladder board lives on ``RaddlePuzzle``."""
    return _raddle_puzzle().build_board(**kwargs)


def prepare_raddle_board_data(**kwargs):
    """Compatibility facade. The ladder board lives on ``RaddlePuzzle``."""
    return _raddle_puzzle().prepare_board(**kwargs)


def _daily_board_adapters():
    WordSaladPuzzle = _word_salad_puzzle()
    RaddlePuzzle = _raddle_puzzle()
    return {
        adapter.task_type: adapter
        for adapter in (
            DailyBoardAdapter(
                WordSaladPuzzle.task_type,
                WordSaladPuzzle.build_board,
                WordSaladPuzzle.prepare_board,
                'word_salad',
                'task-content/task-word-salad.html',
                True,
                False,
                False,
            ),
            DailyBoardAdapter(
                RaddlePuzzle.task_type,
                RaddlePuzzle.build_board,
                RaddlePuzzle.prepare_board,
                'raddle',
                'task-content/task-raddle.html',
                True,
                True,
                False,
            ),
        )
    }


_DAILY_BOARD_ADAPTERS_CACHE = None


def daily_board_adapters():
    global _DAILY_BOARD_ADAPTERS_CACHE
    if _DAILY_BOARD_ADAPTERS_CACHE is None:
        _DAILY_BOARD_ADAPTERS_CACHE = _daily_board_adapters()
    return _DAILY_BOARD_ADAPTERS_CACHE


def get_daily_board_adapter(task_type, *, game_id=None):
    """Return the registered board adapter, with task-type compatibility fallback."""
    definition = get_daily_game(game_id)
    adapter_key = (
        definition.board_adapter_key
        if definition is not None and definition.board_adapter_key
        else task_type
    )
    return daily_board_adapters().get(str(adapter_key or ''))


def __getattr__(name: str):
    if name == 'DAILY_BOARD_ADAPTERS':
        return daily_board_adapters()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
