"""Track B review sheet: dictionary-less base words and their Wiktionary kin.

Advisory only. ``review_sheet`` has little to show for ``absent_common``
gaps (сайт, интернет, витамин): they have no Tikhonov analysis and rarely a
``родств`` nest. For each such gap ranked by corpus frequency, this prints
the lemma's «Родственные слова» and small nests, with every relative's
current game structure, assignment source and corpus count, so a reviewer
decides the family: an opaque Tikhonov root found on the relatives, an own
``root:`` id, or a skip::

    ../venv/interoves_django/bin/python -m \\
        games.puzzles.censorly.offline.tools.coverage_report --gaps /tmp/gaps.tsv
    ../venv/interoves_django/bin/python -m \\
        games.puzzles.censorly.offline.tools.absent_sheet /tmp/gaps.tsv --count 160 --min-articles 3
"""
from __future__ import annotations

import argparse
import csv
import gzip
import os
from collections import defaultdict
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
from django.apps import apps

if not apps.ready:
    django.setup()

from games.puzzles.censorly.lexical.match.core import fold
from games.puzzles.censorly.lexical.match.rootbank import assignment_of, structures_of
from games.puzzles.censorly.offline.corpus import load_corpus

DICTIONARIES = Path(__file__).resolve().parents[2] / 'lexical' / 'data' / 'dictionaries'
MAX_NEST = 40
SHOWN = 14


def _rows(name: str):
    with gzip.open(DICTIONARIES / name, 'rt', encoding='utf-8') as handle:
        next(handle)
        for line in handle:
            yield line.rstrip('\n').split('\t')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('gaps', help='TSV written by coverage_report --gaps')
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--count', type=int, default=150)
    parser.add_argument('--track', default='absent_common')
    parser.add_argument('--min-length', type=int, default=4)
    parser.add_argument(
        '--min-articles', type=int, default=1,
        help='skip lemmas found in fewer corpus articles: they rarely carry over to new ones',
    )
    args = parser.parse_args()

    loaded = load_corpus()
    corpus = loaded.tokens
    with open(args.gaps, encoding='utf-8') as handle:
        gaps = [
            (row['lemma'], int(row['corpus_count']))
            for row in csv.DictReader(handle, delimiter='\t')
            if row['track'] == args.track and len(row['lemma']) >= args.min_length
            and loaded.documents.get(row['lemma'], 0) >= args.min_articles
        ]
    gaps.sort(key=lambda row: -row[1])

    related = defaultdict(set)
    for lemma, _homonym, _block, relative in _rows('wiktionary_related.tsv.gz'):
        lemma, relative = fold(lemma), fold(relative)
        related[lemma].add(relative)
        related[relative].add(lemma)
    nests_of = defaultdict(set)
    members = defaultdict(set)
    for nest, _alternants, _block, lemma in _rows('wiktionary_nests.tsv.gz'):
        lemma = fold(lemma)
        nests_of[lemma].add(nest)
        members[nest].add(lemma)

    def describe(lemma: str) -> str:
        structures = ';'.join('+'.join(item) for item in structures_of(lemma)) or '-'
        source = assignment_of(lemma).get('source', '?')
        return f'{lemma}[{structures}|{source}|{corpus.get(lemma, 0)}]'

    for rank, (lemma, count) in enumerate(gaps[args.start:args.start + args.count], args.start):
        family = set(related.get(lemma, ()))
        for nest in nests_of.get(lemma, ()):
            if len(members[nest]) <= MAX_NEST:
                family |= members[nest]
        family.discard(lemma)
        shown = sorted(family, key=lambda member: (-corpus.get(member, 0), member))[:SHOWN]
        nests = ','.join(sorted(nests_of.get(lemma, ()))) or '-'
        print(f'{rank} {lemma}:{count} nests={nests}')
        if shown:
            print('   ' + ' '.join(describe(member) for member in shown))


if __name__ == '__main__':
    main()
