"""Compatibility alias. Implementation lives in games.puzzles.wall.engine."""
import importlib.util
import sys
from pathlib import Path

# Load the implementation before importing games.puzzles. games.models imports
# Wall from this facade while games.models is still initializing.
_path = Path(__file__).resolve().parent / 'puzzles' / 'wall' / 'engine.py'
_spec = importlib.util.spec_from_file_location('games.puzzles.wall.engine', _path)
_implementation = importlib.util.module_from_spec(_spec)
sys.modules['games.puzzles.wall.engine'] = _implementation
sys.modules[__name__] = _implementation
_spec.loader.exec_module(_implementation)
