"""Sense assignments: lemma → ``sense:*`` (or explicit root) names.

Collected from the two code-data modules, in this order (a later
assignment of the same lemma wins):

1. ``splits.BASE_CLUSTERS``, ``splits.CLUSTERS``, ``splits.MULTI_READINGS``;
2. ``proper_names.CLUSTERS``, ``proper_names.MULTI_READINGS``.

``SEMANTIC_ROOT_ASSIGNMENTS`` gives the root side of a sense lemma that the
dictionaries cannot resolve. The root bank reads both.
"""

from __future__ import annotations

from games.puzzles.censorly.lexical import splits
from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.match import proper_names
from games.puzzles.censorly.lexical.splits import SEMANTIC_ROOT_ASSIGNMENTS


def _collect() -> dict[str, tuple[str, ...]]:
    senses: dict[str, tuple[str, ...]] = {}

    def cluster(lemmas, name):
        for lemma in lemmas:
            senses[fold(lemma)] = (name,)

    def readings(lemma, names):
        senses[fold(lemma)] = tuple(names)

    splits.install(cluster, readings)
    proper_names.install(cluster, readings)
    return senses


SENSES = _collect()

__all__ = ['SEMANTIC_ROOT_ASSIGNMENTS', 'SENSES']
