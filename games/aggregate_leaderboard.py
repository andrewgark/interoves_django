"""Compatibility alias for aggregate results leaderboard builders."""

import sys

from games.results import aggregate_leaderboard as _implementation

sys.modules[__name__] = _implementation
