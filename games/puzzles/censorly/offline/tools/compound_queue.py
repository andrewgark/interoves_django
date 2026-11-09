"""Family review queue for rootless Censorly compounds (roadmap track C).

Advisory only. Groups unresolved multi-component lemmas by their Tikhonov
root tuple (самолёт and самолётный share ``сам+лет``), ranks the groups by
corpus tokens and, for each component, lists the game roots that rooted
single-component members of the same Tikhonov family already carry. A
reviewer picks one reading per component and writes the full structure for
every member to ``reviewed_derived_roots.tsv``::

    ../venv/interoves_django/bin/python -m \
        games.puzzles.censorly.offline.tools.compound_queue --count 150 --tsv /tmp/compounds.tsv

``proposal`` is filled only when every component has one dominant reading
(at least ``--share`` of its evidence); it is still a proposal to review.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import Counter, defaultdict

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
from django.apps import apps

if not apps.ready:
    django.setup()

from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.match.rootbank import (
    provenance,
    root_components,
    structures_of,
    tikhonov_roots_of,
)
from games.puzzles.censorly.lexical.sources import TIKHONOV
from games.puzzles.censorly.offline.corpus import load_corpus
from games.puzzles.censorly.offline.tools.coverage_report import _GAP_SOURCES, _gap_kind


def _component_evidence(families: dict[str, set[str]], corpus) -> dict[str, list]:
    """Tikhonov root -> [(game id, lemma count, example lemmas)] from single-root members."""
    evidence: dict[str, list] = {}
    for root, members in families.items():
        counts: Counter = Counter()
        examples: dict[str, list[str]] = defaultdict(list)
        for lemma in members:
            if tikhonov_roots_of(lemma) != (root,):
                continue
            for structure in root_components(structures_of(lemma)):
                if len(structure) != 1:
                    continue
                counts[structure[0]] += 1
                examples[structure[0]].append(lemma)
        evidence[root] = [
            (item, count, sorted(examples[item], key=lambda w: -corpus.tokens.get(w, 0))[:3])
            for item, count in counts.most_common()
        ]
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--share', type=float, default=0.8)
    parser.add_argument('--tsv', help='also write the window as TSV')
    args = parser.parse_args()

    corpus = load_corpus()
    prov = provenance()
    families: dict[str, set[str]] = defaultdict(set, TIKHONOV.lemmas_by_root())
    tikh_lemmas = set(TIKHONOV.lemmas())

    groups: dict[tuple, list[str]] = defaultdict(list)
    for lemma in tikh_lemmas:
        info = prov.get(lemma) or {}
        if info.get('source') not in _GAP_SOURCES or '-' in lemma:
            continue
        roots = tikhonov_roots_of(lemma)
        if len(roots) < 2 or _gap_kind(lemma, info.get('detail', '')) != 'multi_component':
            continue
        groups[tuple(sorted(roots))].append(lemma)

    ranked = sorted(
        groups.items(),
        key=lambda item: (-sum(corpus.tokens.get(w, 0) for w in item[1]), item[0]),
    )
    window = ranked[args.start:args.start + args.count]
    evidence = _component_evidence(families, corpus)

    rows = []
    for key, members in window:
        members.sort(key=lambda w: (-corpus.tokens.get(w, 0), w))
        tokens = sum(corpus.tokens.get(w, 0) for w in members)
        proposal = []
        for root in key:
            options = evidence.get(root, [])
            total = sum(count for _item, count, _ex in options)
            if options and options[0][1] >= args.share * total:
                proposal.append(options[0][0])
            else:
                proposal = None
                break
        print(f'{"+".join(key)}:{tokens}  ' + ', '.join(
            f'{w}:{corpus.tokens.get(w, 0)}' for w in members[:12]))
        for root in key:
            shown = '; '.join(
                f'{item} x{count} ({", ".join(ex)})' for item, count, ex in evidence.get(root, [])[:4]
            ) or 'NO ROOTED SINGLE-ROOT MEMBER'
            print(f'   {root}: {shown}')
        if proposal:
            print(f'   proposal: {"+".join(proposal)}')
        rows.append({
            'tikhonov_roots': '+'.join(key),
            'corpus_tokens': tokens,
            'members': ' '.join(members),
            'proposal': '+'.join(proposal) if proposal else '',
        })
    if args.tsv:
        with open(args.tsv, 'w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ['tikhonov_roots'], delimiter='\t')
            writer.writeheader()
            writer.writerows(rows)


if __name__ == '__main__':
    main()
