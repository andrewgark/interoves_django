"""Compatibility alias. URL patterns live in games.site.urls.root."""
import sys

from games.site.urls import root as _implementation

sys.modules[__name__] = _implementation
