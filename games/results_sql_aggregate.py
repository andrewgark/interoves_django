"""Compatibility exports for the SQL-backed results aggregation package.

Keep this module as a real module instead of replacing its ``sys.modules``
entry. A stable export surface makes old imports and patches deterministic
while implementation code can depend on ``games.results.sql``.
"""

from games.results.sql import (
    AggregatedAttemptsInfo,
    _attempt_actor_specs,
    get_sql_aggregated_game_actor_rows,
    tasks_need_orm_results_aggregate,
)

__all__ = [
    'get_sql_aggregated_game_actor_rows',
    'tasks_need_orm_results_aggregate',
    'AggregatedAttemptsInfo',
]
