"""External dictionaries: data we did not write and never edit by hand.

Each source is an ``ExternalDictionary``: it knows its files, loads them
lazily once per process and answers lookups. A source never decides
anything about the game; deciders in ``lexical.deciders`` do.
"""

from __future__ import annotations

from games.puzzles.censorly.lexical.sources.base import DICTIONARY_DIR, ExternalDictionary
from games.puzzles.censorly.lexical.sources.kuznetsova import KUZNETSOVA, Kuznetsova
from games.puzzles.censorly.lexical.sources.morphology import MORPHOLOGY, Morphology, Reading
from games.puzzles.censorly.lexical.sources.tikhonov import TIKHONOV, Tikhonov

__all__ = [
    'DICTIONARY_DIR',
    'KUZNETSOVA',
    'MORPHOLOGY',
    'TIKHONOV',
    'ExternalDictionary',
    'Kuznetsova',
    'Morphology',
    'Reading',
    'Tikhonov',
]
