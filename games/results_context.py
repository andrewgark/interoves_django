"""Compatibility facade for the results context domain."""

from games.results.context import (
    attach_results_club_badges,
    club_subscriber_user_ids,
    empty_results_rows_context,
    results_actor_filter_types,
    results_actor_filter_urls,
    results_actor_kind,
    results_column_count,
    results_me_participants,
)

__all__ = [
    'attach_results_club_badges',
    'club_subscriber_user_ids',
    'empty_results_rows_context',
    'results_actor_filter_types',
    'results_actor_filter_urls',
    'results_actor_kind',
    'results_column_count',
    'results_me_participants',
]
