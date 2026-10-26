"""Compatibility alias. Implementation lives in games.site.pages.results.compute."""
import sys

from games.site.pages.results import compute as _implementation

sys.modules[__name__] = _implementation
