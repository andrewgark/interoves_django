"""Write the two Censorly sense review queues under ``lexical/data``.

``sense_review_queue.tsv``: lemmas a sense split left behind. A Kuznetsova
family split into senses (``sense_rules.tsv``, ``sense_assignments.tsv``)
can still hold lemmas with no sense. They keep the bare family root and
open one another but no split sense: an implicit default cluster that may
join unrelated meanings. Resolve a family by listing it in
``sense_family_remainders.tsv`` once its remainder is one meaning (after
moving the lemmas that are not into their senses), or a single lemma with a
sense or ``-`` in ``sense_assignments.tsv``.

``sense_orphan_queue.tsv``: words outside the root dictionary (participles,
reflexives, adverbs, derivatives reached by spelling fallback, a nest
anchor or negation) that stand on the bare root of a family whose every
dictionary lemma has a sense. Nothing in that family shares the bare root,
so the word opens none of its relatives (появиться did not open
появление). Only words seen at least ``ORPHAN_MIN_TOKENS`` times in the
article corpus are listed (about 2 300 more never occur in an article).
Resolve one with the sense of its base, or ``-`` in ``sense_assignments.tsv``
when it rightly stands apart (вид, двадцать). Check the matching snapshot
after a batch: a sense on a word whose forms are also another noun's
(свить / свиты ← свита) can open that noun.

Both are sorted by corpus frequency. Rerun after a change::

    ../venv/interoves_django/bin/python -m \
        games.puzzles.censorly.offline.tools.sense_review_queue

The tests ``test_sense_review_queue_is_current`` and
``test_sense_orphan_queue_is_current`` require the files to match.
"""
from __future__ import annotations

import gzip
from functools import lru_cache
from pathlib import Path

from games.puzzles.censorly.lexical.senses import unsensed_split_family_lemmas

DATA = Path(__file__).resolve().parents[2] / 'lexical' / 'data'
QUEUE = DATA / 'sense_review_queue.tsv'
ORPHAN_QUEUE = DATA / 'sense_orphan_queue.tsv'
COLUMNS = ('family', 'lemma', 'corpus_tokens')
ORPHAN_COLUMNS = ('root', 'lemma', 'corpus_tokens')
ORPHAN_MIN_TOKENS = 1


@lru_cache(maxsize=1)
def _frequencies() -> dict[str, int]:
    with gzip.open(DATA / 'corpus_lemma_freq.tsv.gz', 'rt', encoding='utf-8') as stream:
        next(stream)
        return {
            lemma: int(tokens)
            for lemma, tokens, *_rest in (line.rstrip('\n').split('\t') for line in stream)
        }


def orphan_lemmas() -> set[tuple[str, str]]:
    """(root, lemma) pairs for ``sense_orphan_queue.tsv``."""
    from games.puzzles.censorly.lexical.core import fold
    from games.puzzles.censorly.lexical.deciders.rootbank import ROOT_BANK
    from games.puzzles.censorly.lexical.reviewed import SENSE_ASSIGNMENTS
    from games.puzzles.censorly.lexical.senses import SENSES
    from games.puzzles.censorly.lexical.sources import KUZNETSOVA

    freq = _frequencies()
    structures = ROOT_BANK.built().structures
    dictionary = {fold(lemma) for lemma, _root, _family in KUZNETSOVA.rows()}
    sensed, bare = set(), set()
    for lemma in dictionary:
        for structure in structures.get(lemma, ()):
            if len(structure) == 1:
                bare.add(structure[0])
            elif any(part.startswith('sense:') for part in structure):
                sensed.update(part for part in structure if not part.startswith('sense:'))
    fully_sensed = sensed - bare
    explicit_none = {lemma for lemma, names in SENSE_ASSIGNMENTS.data.items() if not names}
    return {
        (items[0][0], lemma)
        for lemma, items in structures.items()
        if lemma not in SENSES and lemma not in dictionary and lemma not in explicit_none
        and len(items) == 1 and len(items[0]) == 1 and items[0][0] in fully_sensed
        and freq.get(lemma, 0) >= ORPHAN_MIN_TOKENS
    }


def _write(path: Path, columns, pairs) -> None:
    freq = _frequencies()
    rows = sorted(pairs, key=lambda p: (-freq.get(p[1], 0), p[0], p[1]))
    with path.open('w', encoding='utf-8', newline='') as stream:
        stream.write('\t'.join(columns) + '\n')
        for key, lemma in rows:
            stream.write(f'{key}\t{lemma}\t{freq.get(lemma, 0)}\n')
    print(f'{path}: {len(rows)} rows')


def main() -> None:
    _write(QUEUE, COLUMNS, unsensed_split_family_lemmas())
    _write(ORPHAN_QUEUE, ORPHAN_COLUMNS, orphan_lemmas())


if __name__ == '__main__':
    main()
