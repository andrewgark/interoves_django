"""Compatibility alias for shared results/share formatting helpers."""

import sys

from games.results import share as _implementation

sys.modules[__name__] = _implementation
