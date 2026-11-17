"""Write ``lexical/data/sense_review_queue.tsv``: lemmas a sense split left behind.

A Kuznetsova family split into senses (``sense_rules.tsv``,
``sense_assignments.tsv``) can still hold lemmas with no sense. They keep the
bare family root and open one another but no split sense: an implicit
default cluster that may join unrelated meanings. The queue lists them,
most frequent in the article corpus first. Resolve a family by listing it
in ``sense_family_remainders.tsv`` once its remainder is one meaning (after
moving the lemmas that are not into their senses), or a single lemma with a
sense or ``-`` in ``sense_assignments.tsv``, then rerun::

    ../venv/interoves_django/bin/python -m \
        games.puzzles.censorly.offline.tools.sense_review_queue

The test ``test_sense_review_queue_is_current`` requires the file to match.
"""
from __future__ import annotations

import gzip
from pathlib import Path

from games.puzzles.censorly.lexical.senses import unsensed_split_family_lemmas

DATA = Path(__file__).resolve().parents[2] / 'lexical' / 'data'
QUEUE = DATA / 'sense_review_queue.tsv'
COLUMNS = ('family', 'lemma', 'corpus_tokens')


def _frequencies() -> dict[str, int]:
    with gzip.open(DATA / 'corpus_lemma_freq.tsv.gz', 'rt', encoding='utf-8') as stream:
        next(stream)
        return {
            lemma: int(tokens)
            for lemma, tokens, *_rest in (line.rstrip('\n').split('\t') for line in stream)
        }


def main() -> None:
    freq = _frequencies()
    pairs = sorted(unsensed_split_family_lemmas(), key=lambda p: (-freq.get(p[1], 0), p[0], p[1]))
    with QUEUE.open('w', encoding='utf-8', newline='') as stream:
        stream.write('\t'.join(COLUMNS) + '\n')
        for family, lemma in pairs:
            stream.write(f'{family}\t{lemma}\t{freq.get(lemma, 0)}\n')
    print(f'{QUEUE}: {len(pairs)} rows')


if __name__ == '__main__':
    main()
