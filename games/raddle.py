"""Compatibility alias. Implementation lives in games.puzzles.raddle.engine."""
import importlib.util
import sys
from pathlib import Path

# Load the implementation before importing games.puzzles. That package imports
# the raddle puzzle, which imports this facade while the package is initializing.
_path = Path(__file__).resolve().parent / 'puzzles' / 'raddle' / 'engine.py'
_spec = importlib.util.spec_from_file_location('games.puzzles.raddle.engine', _path)
_implementation = importlib.util.module_from_spec(_spec)
sys.modules['games.puzzles.raddle.engine'] = _implementation
sys.modules[__name__] = _implementation
_spec.loader.exec_module(_implementation)
