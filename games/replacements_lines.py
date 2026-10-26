"""Compatibility alias. Implementation lives in games.puzzles.replacements.lines."""
import importlib.util
import sys
from pathlib import Path

# Load the implementation before importing games.puzzles. That package imports
# the replacements puzzle, which imports this facade while the package is initializing.
_dir = Path(__file__).resolve().parent / 'puzzles' / 'replacements' / 'lines'
_path = _dir / '__init__.py'
_spec = importlib.util.spec_from_file_location(
    'games.puzzles.replacements.lines',
    _path,
    submodule_search_locations=[str(_dir)],
)
_implementation = importlib.util.module_from_spec(_spec)
sys.modules['games.puzzles.replacements.lines'] = _implementation
sys.modules[__name__] = _implementation
_spec.loader.exec_module(_implementation)
