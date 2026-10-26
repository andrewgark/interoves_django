"""Compatibility alias. Implementation lives in games.puzzles.replacements.lines.input_parse."""
import sys

from games.puzzles.replacements.lines import input_parse as _implementation

sys.modules[__name__] = _implementation
