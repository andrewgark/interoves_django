"""Sense assignments: lemma → ``sense:*`` readings, and their root identities.

Two reviewed tables decide a lemma's senses (``reviewed.SENSE_RULES`` and
``reviewed.SENSE_ASSIGNMENTS``):

1. ``data/sense_rules.tsv``: ordered regex rules over a whole Kuznetsova
   family. They split a family whose shared historical root hides several
   modern meanings, and they also classify family lemmas nobody listed.
2. ``data/sense_assignments.tsv``: one row per lemma, overriding the rules.
   ``-`` keeps a lemma off every sense. Lemmas in neither table keep their
   dictionary family.

``data/semantic_roots.tsv`` gives the root side of a sense lemma that the
dictionaries cannot resolve: an explicit per-lemma identity, never a sense
name used as an implicit root. Every proper-name cluster member shares one
root (see ``_proper_cluster_roots``).
"""

from __future__ import annotations

from collections import defaultdict

from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.reviewed import SEMANTIC_ROOTS, SENSE_ASSIGNMENTS, SENSE_RULES
from games.puzzles.censorly.lexical.sources import KUZNETSOVA


def _assignments() -> dict[str, tuple[str, ...]]:
    ruled: dict[str, set[str]] = defaultdict(set)
    families = SENSE_RULES.data
    for lemma, _root, family in KUZNETSOVA.rows():
        if family in families:
            sense = SENSE_RULES.classify(family, lemma)
            if sense:
                ruled[fold(lemma)].add(sense)
    found = {lemma: tuple(sorted(names)) for lemma, names in ruled.items()}
    for lemma, names in SENSE_ASSIGNMENTS.data.items():
        if names:
            found[lemma] = names
        else:
            found.pop(lemma, None)
    return found


ASSIGNMENTS = _assignments()

# Views for audits and tests: one-sense clusters and multi-reading lemmas.
_by_sense: dict[str, list[str]] = defaultdict(list)
for _lemma, _names in ASSIGNMENTS.items():
    if len(_names) == 1:
        _by_sense[_names[0]].append(_lemma)
CLUSTERS: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (name, tuple(sorted(lemmas))) for name, lemmas in sorted(_by_sense.items())
)
MULTI_READINGS: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (lemma, names) for lemma, names in sorted(ASSIGNMENTS.items()) if len(names) > 1
)


def install(sense, set_readings) -> None:
    for lemma, names in ASSIGNMENTS.items():
        if len(names) == 1:
            sense((lemma,), names[0])
        else:
            set_readings(lemma, names)


SEMANTIC_ROOT_ASSIGNMENTS: dict[str, tuple[tuple[str, ...], ...]] = dict(SEMANTIC_ROOTS.data)


# Every member of a proper-name cluster shares one root, so Япония opens
# японский and японец.  The root is the one already assigned to a member
# (root:proper_china), or one named after the cluster.  Clusters that keep
# a dictionary family root (Россия with fam:рус², German with fam:нем) are
# left alone, and an explicit per-lemma assignment always wins.
def _proper_cluster_roots() -> dict[str, tuple[tuple[str, ...], ...]]:
    from games.puzzles.censorly.lexical.match.proper_names import CLUSTERS as PROPER_CLUSTERS

    assigned = {}
    for name, lemmas in PROPER_CLUSTERS:
        if name in {'sense:russian', 'sense:german'}:
            continue
        roots = {
            SEMANTIC_ROOT_ASSIGNMENTS[lemma]
            for lemma in lemmas if lemma in SEMANTIC_ROOT_ASSIGNMENTS
        }
        if len(roots) > 1:
            raise RuntimeError(f'proper cluster {name} has several roots: {roots}')
        root = roots.pop() if roots else (('root:proper_' + name.removeprefix('sense:'),),)
        for lemma in lemmas:
            if lemma not in SEMANTIC_ROOT_ASSIGNMENTS:
                assigned[lemma] = root
    return assigned


SEMANTIC_ROOT_ASSIGNMENTS.update(_proper_cluster_roots())
