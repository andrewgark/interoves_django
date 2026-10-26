"""Compatibility alias. Implementation lives in games.console.observatory.explanations."""
import sys

from games.console.observatory import explanations as _implementation

sys.modules[__name__] = _implementation
