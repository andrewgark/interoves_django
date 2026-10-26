"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.audit.prelaunch."""
import sys

from games.puzzles.censorly.lexical.audit import prelaunch as _implementation

sys.modules[__name__] = _implementation
