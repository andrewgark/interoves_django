"""Apply explicitly reviewed assignments to canonical TSV data.

The queue is not runtime input.  This command is the explicit bridge from
APPROVED/APPLIED review decisions to deterministic curated files.
"""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
django.setup()

from games.censorly.lexical.tools.explain_pair import explain_pair
from games.censorly.lexical.core import fold

DATA = Path(__file__).resolve().parents[1] / 'data'
REVIEW_FILE = DATA / 'pair_review.tsv'
FAMILY_REVIEW_FILE = DATA / 'family_review.tsv'
OPAQUE_FILE = DATA / 'reviewed_opaque_roots.tsv'
DERIVED_FILE = DATA / 'reviewed_derived_roots.tsv'
EXISTING_FILE = DATA / 'reviewed_existing_root_assignments.tsv'
DERIVED_REVIEW_FILE = DATA / 'derived_review.tsv'
EXISTING_REVIEW_FILE = DATA / 'existing_root_review.tsv'
RUNTIME_STATUSES = {'APPROVED', 'APPLIED'}


def _read(path):
    with path.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def _write(path, fields, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter='\t', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def _approved_assignments(review_path, family_review_path=FAMILY_REVIEW_FILE,
                          derived_review_path=None, existing_review_path=None):
    opaque = {}
    derived = {}
    existing = {}
    candidates = []
    for row in _read(review_path):
        if row['status'] not in RUNTIME_STATUSES:
            continue
        report = explain_pair(row['guess'], row['target'])
        layer = row['fix_layer']
        source_pair = f'{row["guess"]}/{row["target"]}'
        if layer == 'REVIEWED_OPAQUE_ROOT':
            roots = sorted(set(report['guess']['tikhonov_roots']) | set(report['target']['tikhonov_roots']))
            if not roots:
                raise RuntimeError(f'{source_pair}: opaque assignment has no Tikhonov root')
            for root in roots:
                previous = opaque.get(root)
                note = row['note']
                if previous is not None and previous != note:
                    raise RuntimeError(f'conflicting opaque notes for {root}')
                opaque[root] = note
        elif layer == 'REVIEWED_DERIVED_ROOT':
            sides = (report['guess'], report['target'])
            reviewed_sides = [side for side in sides if side['reviewed_derived']]
            if reviewed_sides:
                source = reviewed_sides[0]
                structures = source['assignment']['structures']
            else:
                rooted = [side for side in sides if side['root_structures']]
                rootless = [side for side in sides if not side['root_structures']]
                if len(rooted) != 1 or len(rootless) != 1:
                    raise RuntimeError(f'{source_pair}: cannot infer complete derived structure')
                source = rootless[0]
                structures = rooted[0]['root_structures']
            for structure in structures:
                key = (source['normalized'], '+'.join(structure))
                note = row['note']
                previous = derived.get(key)
                if previous is not None and previous != note:
                    raise RuntimeError(f'conflicting derived notes for {key}')
                derived[key] = note
        else:
            candidates.append({
                'guess': row['guess'], 'target': row['target'],
                'fix_layer': layer, 'note': row['note'],
            })
    if family_review_path and family_review_path.exists():
        for row in _read(family_review_path):
            if row.get('decision') != 'APPROVE_OPAQUE_FAMILY':
                continue
            root = fold(row.get('root', ''))
            if not root:
                raise RuntimeError('family review contains an empty root')
            note = row.get('note', '')
            previous = opaque.get(root)
            if previous is not None and previous != note:
                raise RuntimeError(f'conflicting family opaque notes for {root}')
            opaque[root] = note
    if derived_review_path and derived_review_path.exists():
        for row in _read(derived_review_path):
            if row.get('decision') != 'APPROVE_DERIVED_ROOT':
                continue
            lemma = fold(row.get('candidate', ''))
            structure = row.get('proposed_assignment', '').strip()
            if not lemma or not structure:
                raise RuntimeError('derived review contains an incomplete explicit assignment')
            key = (lemma, structure)
            note = row.get('note', '')
            previous = derived.get(key)
            if previous is not None and previous != note:
                raise RuntimeError(f'conflicting explicit derived notes for {lemma}/{structure}')
            derived[key] = note
    if existing_review_path and existing_review_path.exists():
        for row in _read(existing_review_path):
            if row.get('decision') != 'APPROVE_EXISTING_ROOT':
                continue
            lemma = fold(row.get('candidate', ''))
            structure = row.get('proposed_assignment', '').strip()
            if not lemma or not structure:
                raise RuntimeError('existing-root review contains an incomplete explicit assignment')
            if not structure.startswith('fam:'):
                raise RuntimeError(f'existing-root assignment is not a fam id: {structure!r}')
            previous = existing.get(lemma)
            note = row.get('note', '')
            if previous is not None and previous != (structure, note):
                raise RuntimeError(f'conflicting existing-root assignment for {lemma}')
            existing[lemma] = (structure, note)
    return opaque, derived, existing, candidates


def _canonical_rows(opaque, derived, existing):
    opaque_rows = [
        {'root_spelling': root, 'note': opaque[root]}
        for root in sorted(opaque)
    ]
    derived_rows = [
        {'lemma': lemma, 'root_structure': structure, 'note': derived[(lemma, structure)]}
        for lemma, structure in sorted(derived)
    ]
    existing_rows = [
        {'lemma': lemma, 'root_structure': structure, 'note': existing[lemma][1]}
        for lemma, (structure, _note) in sorted(existing.items())
    ]
    return opaque_rows, derived_rows, existing_rows


def _check(review_path, family_review_path, derived_review_path=None):
    opaque, derived, existing, _ = _approved_assignments(
        review_path, family_review_path, derived_review_path, EXISTING_REVIEW_FILE,
    )
    expected_opaque, expected_derived, expected_existing = _canonical_rows(opaque, derived, existing)
    actual_opaque = _read(OPAQUE_FILE) if OPAQUE_FILE.exists() else []
    actual_derived = _read(DERIVED_FILE) if DERIVED_FILE.exists() else []
    actual_existing = _read(EXISTING_FILE) if EXISTING_FILE.exists() else []
    problems = []
    if actual_opaque != expected_opaque:
        problems.append('canonical opaque data differs from approved/applied review decisions')
    if actual_derived != expected_derived:
        problems.append('canonical derived data differs from approved/applied review decisions')
    if actual_existing != expected_existing:
        problems.append('canonical existing-root data differs from approved/applied review decisions')
    if not OPAQUE_FILE.exists():
        problems.append(f'missing {OPAQUE_FILE}')
    if not DERIVED_FILE.exists():
        problems.append(f'missing {DERIVED_FILE}')
    if not EXISTING_FILE.exists():
        problems.append(f'missing {EXISTING_FILE}')
    if problems:
        for problem in problems:
            print('DRIFT:', problem)
        return 1
    print('CONSISTENT: review queue and canonical curated files')
    print(f'opaque assignments: {len(actual_opaque)}')
    print(f'derived assignments: {len(actual_derived)}')
    print(f'existing-root assignments: {len(actual_existing)}')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Apply approved reviewed assignments to canonical TSV files.')
    parser.add_argument('--review-file', type=Path, default=REVIEW_FILE)
    parser.add_argument('--family-review-file', type=Path, default=FAMILY_REVIEW_FILE)
    parser.add_argument('--derived-review-file', type=Path, default=DERIVED_REVIEW_FILE)
    parser.add_argument('--existing-review-file', type=Path, default=EXISTING_REVIEW_FILE)
    parser.add_argument('--output-dir', type=Path, default=DATA)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()

    global OPAQUE_FILE, DERIVED_FILE, EXISTING_FILE
    OPAQUE_FILE = args.output_dir / 'reviewed_opaque_roots.tsv'
    DERIVED_FILE = args.output_dir / 'reviewed_derived_roots.tsv'
    EXISTING_FILE = args.output_dir / 'reviewed_existing_root_assignments.tsv'
    if args.check:
        return _check(args.review_file, args.family_review_file, args.derived_review_file)

    opaque, derived, existing, candidates = _approved_assignments(
        args.review_file, args.family_review_file, args.derived_review_file,
        args.existing_review_file,
    )
    opaque_rows, derived_rows, existing_rows = _canonical_rows(opaque, derived, existing)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write(OPAQUE_FILE, ('root_spelling', 'note'), opaque_rows)
    _write(DERIVED_FILE, ('lemma', 'root_structure', 'note'), derived_rows)
    _write(EXISTING_FILE, ('lemma', 'root_structure', 'note'), existing_rows)
    _write(args.output_dir / 'review_candidates.generated.tsv',
           ('guess', 'target', 'fix_layer', 'note'), candidates)
    manifest = [
        {
            'guess': row['guess'], 'target': row['target'],
            'fix_layer': row['fix_layer'], 'status': row['status'],
            'generated': 'assignment-layer' if row['fix_layer'] in {
                'REVIEWED_OPAQUE_ROOT', 'REVIEWED_DERIVED_ROOT'
            } else 'candidate-only',
        }
        for row in _read(args.review_file)
        if row['status'] in RUNTIME_STATUSES
    ]
    if args.family_review_file.exists():
        manifest.extend({
            'guess': row.get('root', ''), 'target': '',
            'fix_layer': 'REVIEWED_OPAQUE_ROOT', 'status': row.get('decision', ''),
            'generated': 'assignment-layer',
        } for row in _read(args.family_review_file)
        if row.get('decision') == 'APPROVE_OPAQUE_FAMILY')
    _write(args.output_dir / 'review_apply_manifest.tsv',
           ('guess', 'target', 'fix_layer', 'status', 'generated'),
           manifest)
    print(f'processed explicit decisions: {len(opaque_rows) + len(derived_rows) + len(existing_rows)} assignments')
    print(f'generated: {OPAQUE_FILE}')
    print(f'generated: {DERIVED_FILE}')
    print(f'generated: {EXISTING_FILE}')
    print('No pair edges or resolver source files were changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

