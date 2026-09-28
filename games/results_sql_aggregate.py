"""Compatibility alias for SQL-backed results aggregation."""

import sys

from games.results import sql_aggregate as _implementation

sys.modules[__name__] = _implementation
