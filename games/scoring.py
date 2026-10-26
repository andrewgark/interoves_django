"""Compatibility alias. Implementation lives in games.daily.completion.scoring."""
import sys

from games.daily.completion import scoring as _implementation

sys.modules[__name__] = _implementation
