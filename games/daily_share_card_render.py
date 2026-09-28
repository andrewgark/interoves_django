"""Compatibility alias for the daily share-card renderer."""

import sys

from games.daily import share_card_render as _implementation

sys.modules[__name__] = _implementation
