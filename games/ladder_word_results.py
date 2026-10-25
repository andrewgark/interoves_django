"""Compatibility alias for ladder word result builders."""

import sys

from games.results import ladder as _implementation

sys.modules[__name__] = _implementation
