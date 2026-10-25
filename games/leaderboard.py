"""Compatibility alias for shared results leaderboard semantics."""

import sys

from games.results.standings import leaderboard as _implementation

sys.modules[__name__] = _implementation
