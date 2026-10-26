"""Compatibility alias. Implementation lives in games.ladder.tutorial.views."""
import sys

from games.ladder.tutorial import views as _implementation

sys.modules[__name__] = _implementation
