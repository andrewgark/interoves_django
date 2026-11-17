"""Sense assignments: lemma → ``sense:*`` (or explicit root) names.

Collected in this order (a later assignment of the same lemma wins):

1. ``splits``: ``data/sense_rules.tsv`` and ``data/sense_assignments.tsv``;
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


def unsensed_split_family_lemmas() -> set[tuple[str, str]]:
    """(family, lemma) pairs left without a sense in a family split by sense.

    A Kuznetsova family is split when its lemmas carry two or more sense
    names. Its lemmas with no sense keep the bare family root and open each
    other (друг/дружба), but none of the split senses: an implicit default
    cluster nobody reviewed. Until a lemma gets a sense or an explicit ``-``
    row in ``sense_assignments.tsv`` (reviewed: it belongs to that default
    cluster), it is listed in ``data/sense_review_queue.tsv``.
    """
    from collections import defaultdict

    from games.puzzles.censorly.lexical.reviewed import SENSE_ASSIGNMENTS
    from games.puzzles.censorly.lexical.sources import KUZNETSOVA

    members: dict[str, set[str]] = defaultdict(set)
    for lemma, _root, family in KUZNETSOVA.rows():
        members[family].add(fold(lemma))
    explicit_none = {lemma for lemma, names in SENSE_ASSIGNMENTS.data.items() if not names}
    found = set()
    for family, lemmas in members.items():
        names = {name for lemma in lemmas for name in SENSES.get(lemma, ())}
        if len(names) < 2:
            continue
        for lemma in lemmas:
            if lemma not in SENSES and lemma not in explicit_none:
                found.add((family, lemma))
    return found
