"""Compatibility facade for the task result details helpers."""

from games.tasks.result_details import (
    SaladResultHeader,
    SaladResultWord,
    chain_state_for_actor,
    latest_actor_task_state,
    raddle_ui_state_for_actor,
    set_current_result_header_answers,
    word_salad_release_breakdown,
)

__all__ = [
    'SaladResultHeader',
    'SaladResultWord',
    'chain_state_for_actor',
    'latest_actor_task_state',
    'raddle_ui_state_for_actor',
    'set_current_result_header_answers',
    'word_salad_release_breakdown',
]
