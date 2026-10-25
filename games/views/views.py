"""Compatibility alias. Implementation lives in games.site.views.pages.legacy."""
import sys

from games.site.views.pages import legacy as _implementation

sys.modules[__name__] = _implementation
