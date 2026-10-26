"""Compatibility alias. Implementation lives in games.puzzles.censorly.offline.audit.inflection."""
import sys

from games.puzzles.censorly.offline.audit import inflection as _implementation

sys.modules[__name__] = _implementation
