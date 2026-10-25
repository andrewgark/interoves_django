"""Compatibility alias for daily share-card payload helpers."""

import sys

from games.daily.page.share import card as _implementation

sys.modules[__name__] = _implementation
