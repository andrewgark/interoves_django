"""Compatibility alias. Implementation lives in games.site.views.pages.ui_exports."""
import sys

from games.site.views.pages import ui_exports as _implementation

sys.modules[__name__] = _implementation
