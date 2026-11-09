"""Track E review sheet: roots given by unique spelling alone.

Advisory only. A lemma with no Kuznetsova membership whose Tikhonov morph
matches exactly one game root by spelling gets that root
(``UNIQUE_SPELLING_FALLBACK``). A wrong match is a false opening for every
lemma with that morph, so the sheet groups fallback lemmas by
``morph -> root``, ranks groups by corpus tokens, and shows each group's
lemmas next to the reviewed family it joined. ``nest`` counts lemmas that
share a ru.wiktionary ``родств`` nest with a reviewed member of the root:
independent evidence that the spelling match is the same root::

    ../venv/interoves_django/bin/python -m \\
        games.puzzles.censorly.offline.tools.fallback_audit --count 100 --tsv /tmp/e.tsv
"""
from __future__ import annotations

import argparse
import csv
import gzip
import os
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
from django.apps import apps

if not apps.ready:
    django.setup()

from games.puzzles.censorly.lexical.match.core import fold
from games.puzzles.censorly.lexical.match.rootbank import provenance, structures_of, tikhonov_roots_of
from games.puzzles.censorly.offline.corpus import load_corpus

DICTIONARIES = Path(__file__).resolve().parents[2] / 'lexical' / 'data' / 'dictionaries'
FALLBACK = 'UNIQUE_SPELLING_FALLBACK'
REVIEWED = ('KUZNETSOVA', 'KUZNETSOVA_MULTI', 'SEMANTIC_SPLIT_ROOTED', 'REVIEWED_DERIVED_ROOT',
            'REVIEWED_EXISTING_ROOT', 'SEMANTIC_ROOT_ASSIGNMENT')


def _nests() -> dict[str, set[str]]:
    nests = defaultdict(set)
    with gzip.open(DICTIONARIES / 'wiktionary_nests.tsv.gz', 'rt', encoding='utf-8') as handle:
        next(handle)
        for line in handle:
            nest, _alternants, _block, lemma = line.rstrip('\n').split('\t')
            nests[fold(lemma)].add(nest)
    return nests


def _ids(structures) -> set[str]:
    return {
        item for structure in structures for item in structure
        if not item.startswith('sense:')
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--tsv', type=Path, help='also write every group as TSV')
    args = parser.parse_args()

    corpus = load_corpus().tokens
    nests = _nests()
    sources = provenance()
    members = defaultdict(list)
    groups = defaultdict(list)
    for lemma, info in sources.items():
        source = info.get('source')
        structures = structures_of(lemma)
        if source in REVIEWED:
            for root in _ids(structures):
                members[root].append(lemma)
        elif source == FALLBACK:
            key = ('+'.join(tikhonov_roots_of(lemma)), ';'.join('+'.join(s) for s in structures))
            groups[key].append(lemma)

    member_nests = {
        root: set().union(*(nests.get(lemma, set()) for lemma in lemmas))
        for root, lemmas in members.items()
    }
    rows = []
    for (morph, structure), lemmas in groups.items():
        roots = {item for item in structure.replace(';', '+').split('+') if item and not item.startswith('sense:')}
        known = set().union(*(member_nests.get(root, set()) for root in roots)) if roots else set()
        confirmed = sum(1 for lemma in lemmas if nests.get(lemma, set()) & known)
        tokens = sum(corpus.get(lemma, 0) for lemma in lemmas)
        lemmas.sort(key=lambda lemma: (-corpus.get(lemma, 0), lemma))
        family = sorted(
            {lemma for root in roots for lemma in members.get(root, ())},
            key=lambda lemma: (-corpus.get(lemma, 0), lemma),
        )
        rows.append((tokens, morph, structure, lemmas, confirmed, family))
    rows.sort(key=lambda row: (-row[0], row[1]))

    total = Counter(source.get('source') for source in sources.values())[FALLBACK]
    print(f'fallback_lemmas={total} groups={len(rows)} '
          f'corpus_tokens={sum(row[0] for row in rows)}')
    for rank, (tokens, morph, structure, lemmas, confirmed, family) in enumerate(
        rows[args.start:args.start + args.count], args.start,
    ):
        shown = ', '.join(f'{lemma}:{corpus.get(lemma, 0)}' for lemma in lemmas[:8])
        print(f'{rank} {morph} -> {structure} tokens={tokens} lemmas={len(lemmas)} nest={confirmed}')
        print(f'   fallback: {shown}{" …" if len(lemmas) > 8 else ""}')
        print(f'   family:   {", ".join(family[:8]) or "-"}')
    if args.tsv:
        with args.tsv.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
            writer.writerow(['morph', 'structure', 'corpus_tokens', 'lemmas', 'nest_confirmed',
                             'fallback_lemmas', 'family_sample'])
            for tokens, morph, structure, lemmas, confirmed, family in rows:
                writer.writerow([morph, structure, tokens, len(lemmas), confirmed,
                                 ','.join(lemmas), ','.join(family[:12])])


if __name__ == '__main__':
    main()
