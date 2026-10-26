"""Compatibility alias. Implementation lives in games.puzzles.replacements.forms."""
import sys

from games.puzzles.replacements import forms as _implementation

sys.modules[__name__] = _implementation
