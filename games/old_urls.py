"""Compatibility alias. URL patterns live in games.site.urls.old."""
import sys

from games.site.urls import old as _implementation

sys.modules[__name__] = _implementation
