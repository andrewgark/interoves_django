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



def _family_members() -> dict[str, set[str]]:
    from collections import defaultdict

    from games.puzzles.censorly.lexical.sources import KUZNETSOVA

    members: dict[str, set[str]] = defaultdict(set)
    for lemma, _root, family in KUZNETSOVA.rows():
        members[family].add(fold(lemma))
    return members


def split_families() -> set[str]:
    """Kuznetsova families whose lemmas carry two or more sense names."""
    return {
        family for family, lemmas in _family_members().items()
        if len({name for lemma in lemmas for name in SENSES.get(lemma, ())}) >= 2
    }


def reviewed_family_remainders() -> dict[str, str]:
    """family → note from ``data/sense_family_remainders.tsv``."""
    import csv

    from games.puzzles.censorly.lexical.reviewed.table import DATA_DIR

    with (DATA_DIR / 'sense_family_remainders.tsv').open(encoding='utf-8', newline='') as stream:
        return {row['family']: row['note'] for row in csv.DictReader(stream, delimiter='\t')}


def unsensed_split_family_lemmas() -> set[tuple[str, str]]:
    """(family, lemma) pairs left without a sense in a family split by sense.

    A split family's lemmas with no sense keep the bare family root and open
    each other (друг/дружба), but none of the split senses: an implicit
    default cluster. So do lemmas outside Kuznetsova that reach the family
    root another way (нерешительный, степенной), which is why the remainder
    is not given a sense name of its own. A family whose remainder was
    reviewed as one meaning is listed in ``sense_family_remainders.tsv``; a
    single lemma is settled with a sense or a ``-`` row in
    ``sense_assignments.tsv``. Everything else is queued in
    ``data/sense_review_queue.tsv``.
    """
    from games.puzzles.censorly.lexical.reviewed import SENSE_ASSIGNMENTS

    members = _family_members()
    reviewed = reviewed_family_remainders()
    explicit_none = {lemma for lemma, names in SENSE_ASSIGNMENTS.data.items() if not names}
    return {
        (family, lemma)
        for family in split_families() - set(reviewed)
        for lemma in members[family]
        if lemma not in SENSES and lemma not in explicit_none
    }
