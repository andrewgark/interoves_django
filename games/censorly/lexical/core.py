"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.match.core."""
import importlib.util
import sys
from pathlib import Path

# Load the implementation file directly. Importing its package first runs a
# package init that imports this facade again.
_path = Path(__file__).resolve().parents[2] / 'puzzles' / 'censorly' / 'lexical' / 'match' / 'core.py'
_spec = importlib.util.spec_from_file_location('games.puzzles.censorly.lexical.match.core', _path)
_implementation = importlib.util.module_from_spec(_spec)
sys.modules['games.puzzles.censorly.lexical.match.core'] = _implementation
sys.modules[__name__] = _implementation
_spec.loader.exec_module(_implementation)
