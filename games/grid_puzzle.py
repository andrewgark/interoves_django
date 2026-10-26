"""Compatibility alias. Implementation lives in games.puzzles.replacements.grid_puzzle."""
import importlib.util
import sys
from pathlib import Path

# Load the implementation before importing games.puzzles. The grid puzzle class
# imports this facade at module level while the package may still be initializing.
_path = Path(__file__).resolve().parent / 'puzzles' / 'replacements' / 'grid_puzzle.py'
_spec = importlib.util.spec_from_file_location('games.puzzles.replacements.grid_puzzle', _path)
_implementation = importlib.util.module_from_spec(_spec)
sys.modules['games.puzzles.replacements.grid_puzzle'] = _implementation
sys.modules[__name__] = _implementation
_spec.loader.exec_module(_implementation)
