"""Compatibility alias for the daily share-card renderer."""

import sys

from games.daily.page.share import render as _implementation

sys.modules[__name__] = _implementation
