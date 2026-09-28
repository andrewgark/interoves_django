"""Compatibility facade for results table helpers."""

from games.results.tables import (
    ResultsTaskGroupHeader,
    load_results_placements_and_tasks,
    results_table_headers_context,
)

__all__ = [
    'ResultsTaskGroupHeader',
    'load_results_placements_and_tasks',
    'results_table_headers_context',
]
