"""Compatibility alias. Implementation lives in games.puzzles.censorly.text.stopwords."""
import sys

from games.puzzles.censorly.text import stopwords as _implementation

sys.modules[__name__] = _implementation
