"""Compatibility alias. URL patterns live in games.site.urls.ui."""
import sys

from games.site.urls import ui as _implementation

sys.modules[__name__] = _implementation
