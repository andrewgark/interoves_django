"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.splits."""
import sys

from games.puzzles.censorly.lexical import splits as _implementation

sys.modules[__name__] = _implementation
