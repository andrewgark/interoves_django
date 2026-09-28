"""Compatibility facade for daily statistics helpers."""

from games.daily.statistics import (
    CACHE_TIMEOUT,
    CACHE_VERSION,
    POPULAR_LIMIT,
    POPULAR_MIN_ENTRIES,
    build_attempt_histogram,
    build_daily_statistics,
    cache_key,
    invalidate_daily_statistics,
)

__all__ = [
    'CACHE_TIMEOUT',
    'CACHE_VERSION',
    'POPULAR_LIMIT',
    'POPULAR_MIN_ENTRIES',
    'build_attempt_histogram',
    'build_daily_statistics',
    'cache_key',
    'invalidate_daily_statistics',
]
