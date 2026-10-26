"""Compatibility alias. Implementation lives in games.daily.completion.completion_coordinator."""
import sys

from games.daily.completion import completion_coordinator as _implementation

sys.modules[__name__] = _implementation
