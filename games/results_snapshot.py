"""Compatibility alias for results snapshot builders."""

import sys

from games.results.snapshots import snapshot as _implementation

sys.modules[__name__] = _implementation
