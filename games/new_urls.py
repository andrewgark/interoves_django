"""Compatibility alias. URL patterns live in games.site.urls.new."""
import sys

from games.site.urls import new as _implementation

sys.modules[__name__] = _implementation
