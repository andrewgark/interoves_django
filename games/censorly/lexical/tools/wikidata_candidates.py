"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.tools.candidates.wikidata_candidates."""
import sys

from games.puzzles.censorly.lexical.tools.candidates import wikidata_candidates as _implementation

sys.modules[__name__] = _implementation
