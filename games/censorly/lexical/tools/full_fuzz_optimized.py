"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.tools.fuzz.full_fuzz_optimized."""
import sys

from games.puzzles.censorly.lexical.tools.fuzz import full_fuzz_optimized as _implementation

sys.modules[__name__] = _implementation
