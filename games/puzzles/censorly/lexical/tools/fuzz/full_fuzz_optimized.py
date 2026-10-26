"""Compatibility alias. Implementation lives in games.puzzles.censorly.offline.tools.fuzz.full_fuzz_optimized."""
import sys

from games.puzzles.censorly.offline.tools.fuzz import full_fuzz_optimized as _implementation

sys.modules[__name__] = _implementation
