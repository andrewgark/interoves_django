"""Compatibility alias. Implementation lives in games.ladder.tutorial.ladder."""
import sys

from games.ladder.tutorial import ladder as _implementation

sys.modules[__name__] = _implementation
