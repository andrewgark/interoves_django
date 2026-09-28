"""Compatibility alias for daily share-card payload helpers."""

import sys

from games.daily import share_card as _implementation

sys.modules[__name__] = _implementation
