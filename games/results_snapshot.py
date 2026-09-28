"""Compatibility alias for results snapshot builders."""

import sys

from games.results import snapshot as _implementation

sys.modules[__name__] = _implementation
