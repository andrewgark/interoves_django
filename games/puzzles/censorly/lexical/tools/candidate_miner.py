"""Compatibility alias. Implementation lives in games.puzzles.censorly.offline.tools.candidate_miner."""
import sys

from games.puzzles.censorly.offline.tools import candidate_miner as _implementation

sys.modules[__name__] = _implementation
