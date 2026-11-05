"""Compare extracted ru.wiktionary data with the Censorly root bank.

Advisory only. It answers the W1 questions of the dictionary roadmap: how
many lemmas Wiktionary adds, how often its roots agree with Tikhonov, and
how many current gaps its segmentation would close and by which rule::

    ../venv/interoves_django/bin/python -m \
        games.puzzles.censorly.offline.tools.wiktionary.compare \
        --gaps-out /tmp/wiktionary-gap-candidates.tsv
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
    _KUZ_GROUPS,
    _TIKHONOV,
    _candidate_ids,
    _families,
    _lines,
    _plain,
    _reviewed_opaque_id,
    _strip_note,
    families_of,
    provenance,
)

_GAPS = frozenset({'UNRESOLVED', 'UNRESOLVED_TOO_AMBIGUOUS', 'ABSENT'})


def _roots(segmentation: str) -> tuple[str, ...]:
    return tuple(
        _plain(part.rsplit(':', 1)[0])
        for part in segmentation.split('/')
        if part.endswith(':ROOT')
    )


def load_morphs(path: Path):
    """Common-noun lemmas only: capitalized titles are proper names."""
    by_lemma: dict[str, list[dict]] = defaultdict(list)
    proper: set[str] = set()
    with gzip.open(path, 'rt', encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream, delimiter='\t'):
            lemma = row['lemma']
            if lemma[:1].isupper():
                proper.add(fold(lemma))
                continue
            by_lemma[fold(lemma)].append(row)
    return by_lemma, proper


def load_nests(path: Path) -> dict[str, set[str]]:
    nests: dict[str, set[str]] = defaultdict(set)
    if path.exists():
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for row in csv.DictReader(stream, delimiter='\t'):
                nests[fold(row['lemma'])].add(row['nest'])
    return nests


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dir', type=Path, default=_DICT_DIR)
    parser.add_argument('--gaps-out', type=Path)
    args = parser.parse_args()

    morphs, proper = load_morphs(args.dir / 'wiktionary_morphs.tsv.gz')
    nests = load_nests(args.dir / 'wiktionary_nests.tsv.gz')
    for lemma, rows in morphs.items():
        for row in rows:
            nests[lemma].update(item for item in row['nests'].split(',') if item)

    tikhonov: dict[str, tuple[str, ...]] = {}
    for line in _lines(_TIKHONOV):
        raw, seg = line.split('\t', 1)
        tikhonov[fold(_strip_note(raw))] = tuple(
            _plain(part.rsplit(':', 1)[0]) for part in seg.split('/') if part.endswith(':ROOT')
        )
    prov = provenance()

    families = _families(_KUZ_GROUPS)
    morph_family, plain_hits = {}, {}
    for morph, family in families.items():
        morph_family[morph] = family
        plain_hits.setdefault(_plain(morph), []).append((morph, family))
    family_members: dict[str, set[str]] = defaultdict(set)
    for lemma in prov:
        for family in families_of(lemma):
            family_members['fam:' + family].add(lemma)

    stats = Counter()
    agree = Counter()
    rows_out = []
    for lemma, rows in morphs.items():
        stats['lemmas'] += 1
        stats['homonymous'] += len({row['homonym'] for row in rows}) > 1
        source = (prov.get(lemma) or {}).get('source', 'ABSENT')
        if lemma in tikhonov:
            stats['in_tikhonov'] += 1
            wk = {tuple(sorted(_roots(row['segmentation']))) for row in rows}
            agree['same_roots' if tuple(sorted(tikhonov[lemma])) in wk else 'different_roots'] += 1
        else:
            stats['new_vs_tikhonov'] += 1
            stats['new_vs_tikhonov_and_bank'] += lemma not in prov
        if source not in _GAPS:
            continue
        stats[f'gap_{source}'] += 1
        segs = {row['segmentation'] for row in rows}
        if len(segs) > 1:
            verdict, detail = 'REVIEW_HOMONYMS', ' | '.join(sorted(segs))
        else:
            seg = next(iter(segs))
            kinds, ids = [], []
            for root in _roots(seg):
                candidates = _candidate_ids(root, plain_hits, morph_family)
                opaque = _reviewed_opaque_id(root, plain_hits)
                if opaque:
                    kinds.append('opaque')
                    ids.append(opaque)
                elif len(candidates) == 1:
                    confirmed = any(
                        nests[lemma] & nests[member]
                        for member in family_members.get(candidates[0], ())
                    )
                    kinds.append('unique_nest' if confirmed else 'unique_unconfirmed')
                    ids.append(candidates[0])
                elif candidates:
                    kinds.append('ambiguous')
                    ids.append('|'.join(candidates))
                else:
                    kinds.append('no_root')
                    ids.append('tikh?:' + root)
            if all(kind in ('opaque', 'unique_nest') for kind in kinds):
                verdict = 'AUTO_CONFIRMED'
            elif all(kind in ('opaque', 'unique_nest', 'unique_unconfirmed') for kind in kinds):
                verdict = 'UNIQUE_UNCONFIRMED'
            elif 'ambiguous' in kinds:
                verdict = 'REVIEW_AMBIGUOUS'
            else:
                verdict = 'NEEDS_OPAQUE'
            detail = f'{seg} -> {"+".join(ids)}'
        stats[f'verdict_{verdict}'] += 1
        rows_out.append((lemma, source, verdict, detail, ','.join(sorted(nests[lemma]))))

    bank_gaps = sum(1 for item in prov.values() if item['source'] in _GAPS)
    print(f'wiktionary common lemmas: {stats["lemmas"]} (+{len(proper)} capitalized/proper), '
          f'homonymous: {stats["homonymous"]}')
    print(f'  in Tikhonov file: {stats["in_tikhonov"]}, root agreement: '
          f'{agree["same_roots"]} same / {agree["different_roots"]} different')
    print(f'  not in Tikhonov file: {stats["new_vs_tikhonov"]}, '
          f'of them not in the bank at all: {stats["new_vs_tikhonov_and_bank"]}')
    print(f'bank gaps (UNRESOLVED) covered: {stats["gap_UNRESOLVED"]} of {bank_gaps}; '
          f'ABSENT lemmas gained: {stats["gap_ABSENT"]}')
    for key in sorted(stats):
        if key.startswith('verdict_'):
            print(f'  {key[8:]:20s} {stats[key]}')
    print(f'nests: {len({nest for items in nests.values() for nest in items})} '
          f'with {sum(map(len, nests.values()))} memberships')
    if args.gaps_out:
        with args.gaps_out.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream, delimiter='\t')
            writer.writerow(['lemma', 'bank_source', 'verdict', 'detail', 'nests'])
            writer.writerows(sorted(rows_out, key=lambda row: (row[2], row[0])))
        print(f'gap candidates written to {args.gaps_out}')


if __name__ == '__main__':
    main()
