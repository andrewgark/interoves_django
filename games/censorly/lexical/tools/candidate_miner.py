"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.tools.candidate_miner."""
import sys

from games.puzzles.censorly.lexical.tools import candidate_miner as _implementation

sys.modules[__name__] = _implementation
