"""Compatibility alias. Implementation lives in games.puzzles.alphabetty.dicts."""
import importlib.util
import sys
from pathlib import Path

# Load the implementation before importing games.puzzles. That package import
# needs games.models, which is still initializing this facade.
_path = Path(__file__).resolve().parents[1] / 'puzzles' / 'alphabetty' / 'dicts.py'
_spec = importlib.util.spec_from_file_location('games.puzzles.alphabetty.dicts', _path)
_implementation = importlib.util.module_from_spec(_spec)
sys.modules['games.puzzles.alphabetty.dicts'] = _implementation
sys.modules[__name__] = _implementation
_spec.loader.exec_module(_implementation)
