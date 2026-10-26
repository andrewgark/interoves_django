"""Compatibility alias. Checker registry lives in games.puzzles.core.check_runtime."""
import sys

from games.puzzles.core import check_runtime as _implementation

sys.modules[__name__] = _implementation
