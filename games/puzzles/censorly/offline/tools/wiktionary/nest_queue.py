"""W4 review queue: rootless lemmas and their rooted ru.wiktionary nest-mates.

Advisory only. For every lemma without game roots that sits in a
``родств:*`` nest, propose the rooted nest-mate whose structures it should
copy (a ``reviewed_nest_anchors.tsv`` row). The proposal is a ranking, not a
decision: nests are etymological, so «завидный» sits in the «вид» nest.

    ../venv/interoves_django/bin/python -m \
        games.puzzles.censorly.offline.tools.wiktionary.nest_queue \
        --output /tmp/nest-queue.tsv [--all]
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
from games.puzzles.censorly.lexical.match.rootbank import (
    _DICT_DIR,
    NEST_ANCHORS,
    provenance,
    structures_of,
)
from games.puzzles.censorly.offline.corpus import load_corpus

REJECTED = Path(__file__).resolve().parents[3] / 'lexical' / 'data' / 'rejected_nest_anchors.tsv'


def _read_gz(path: Path):
    with gzip.open(path, 'rt', encoding='utf-8', newline='') as stream:
        yield from csv.DictReader(stream, delimiter='\t')


def load_nests(directory: Path):
    """Nest members, each lemma's nests, and its Wiktionary root counts.

    A lemma's own page names its nests directly; that beats being listed
    in some other nest template, so own-page nests replace listings.
    """
    members: dict[str, set[str]] = defaultdict(set)
    listed: dict[str, set[str]] = defaultdict(set)
    own: dict[str, set[str]] = defaultdict(set)
    roots: dict[str, set[int]] = defaultdict(set)
    for row in _read_gz(directory / 'wiktionary_nests.tsv.gz'):
        lemma = fold(row['lemma'])
        members[row['nest']].add(lemma)
        listed[lemma].add(row['nest'])
    for row in _read_gz(directory / 'wiktionary_morphs.tsv.gz'):
        if row['lemma'][:1].isupper():
            continue
        lemma = fold(row['lemma'])
        roots[lemma].add(row['segmentation'].count(':ROOT'))
        for nest in filter(None, row['nests'].split(',')):
            members[nest].add(lemma)
            own[lemma].add(nest)
    nests_of = {lemma: own.get(lemma) or nests for lemma, nests in listed.items()}
    nests_of.update(own)
    return members, nests_of, roots


def load_related(directory: Path) -> dict[str, set[str]]:
    related: dict[str, set[str]] = defaultdict(set)
    for row in _read_gz(directory / 'wiktionary_related.tsv.gz'):
        left, right = fold(row['lemma']), fold(row['related'])
        related[left].add(right)
        related[right].add(left)
    return related


def rejected_pairs() -> set[tuple[str, str]]:
    if not REJECTED.exists():
        return set()
    with REJECTED.open(encoding='utf-8', newline='') as stream:
        return {
            (fold(row['lemma']), fold(row['anchor']))
            for row in csv.DictReader(stream, delimiter='\t')
        }


def _prefix(left: str, right: str) -> int:
    size = 0
    for a, b in zip(left, right):
        if a != b:
            break
        size += 1
    return size


def _score(lemma: str, anchor: str, related: dict[str, set[str]]) -> int:
    structures = structures_of(anchor)
    score = 2 * _prefix(lemma, anchor)
    if anchor in related.get(lemma, ()):
        score += 6
    # A compound anchor (several roots) rarely is the base of a plain word.
    if all(
        len([part for part in item if not part.startswith('sense:')]) > 1
        for item in structures
    ):
        score -= 8
    return score - abs(len(anchor) - len(lemma)) // 3


def _fmt(structures) -> str:
    return ';'.join('+'.join(item) for item in structures)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dir', type=Path, default=_DICT_DIR)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--all', action='store_true', help='not only corpus lemmas')
    args = parser.parse_args()

    prov = provenance()
    members, nests_of, wk_roots = load_nests(args.dir)
    related = load_related(args.dir)
    counts = load_corpus().tokens
    rejected = rejected_pairs()

    rows = []
    stats = Counter()
    for lemma, nests in nests_of.items():
        if not lemma.isalpha() or structures_of(lemma) or lemma in NEST_ANCHORS:
            continue
        count = counts.get(lemma, 0)
        if not count and not args.all:
            continue
        stats['rootless_in_nest'] += 1
        if max(wk_roots.get(lemma) or {1}) > 1:
            # A compound copies no single member: «три» must not open
            # «треугольник». Compounds need a full structure (track C).
            stats['compound_skipped'] += 1
            continue
        mates = {
            mate for nest in nests for mate in members[nest]
            if mate != lemma and structures_of(mate)
            and (lemma, mate) not in rejected
        }
        if not mates:
            stats['no_rooted_mate'] += 1
            continue
        ranked = sorted(mates, key=lambda mate: (-_score(lemma, mate, related), mate))
        best = ranked[0]
        families = Counter(_fmt(structures_of(mate)) for mate in mates)
        rows.append({
            'lemma': lemma,
            'corpus_count': count,
            'bank_source': (prov.get(lemma) or {}).get('source', 'ABSENT'),
            'anchor': best,
            'anchor_structures': _fmt(structures_of(best)),
            'score': _score(lemma, best, related),
            'related': 'yes' if best in related.get(lemma, ()) else '',
            'nests': ','.join(sorted(nests)),
            'mates_rooted': len(mates),
            'nest_structures': ' | '.join(
                f'{structure}×{n}' for structure, n in families.most_common(4)
            ),
            'alternatives': ', '.join(
                f'{mate}[{_fmt(structures_of(mate))}]' for mate in ranked[1:5]
            ),
        })
        stats['proposed'] += 1

    rows.sort(key=lambda row: (-row['corpus_count'], -row['score'], row['lemma']))
    with args.output.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ['lemma'], delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)
    print(dict(stats), f'-> {args.output}')


if __name__ == '__main__':
    main()
