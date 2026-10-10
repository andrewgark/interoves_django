"""High-precision advisory ranking over candidate-miner TSV artifacts.

This is deliberately a second, offline stage.  It does not analyze the
dictionary, call the resolver, or modify review/runtime data.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


def read_tsv(path: Path):
    with path.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def write_tsv(path: Path, rows, fields=None):
    rows = list(rows)
    if fields is None:
        fields = list(rows[0]) if rows else []
        for row in rows[1:]:
            for field in row:
                if field not in fields:
                    fields.append(field)
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter='\t', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def _frequency(row):
    try:
        return int(row.get('corpus_frequency', 0) or 0)
    except ValueError:
        return 0


def _members(row):
    return [item for item in (row.get('members') or row.get('family') or '').split('|') if item]


def _semantic(row):
    return bool(row.get('semantic_conflicts')) or 'semantic_conflict' in (row.get('risk_flags') or '')


def _opaque(row):
    members = _members(row)
    root = row.get('root_spelling', '')
    flags = set(filter(None, (row.get('risk_flags') or '').split('|')))
    if len(members) > 8:
        flags.add('LARGE_FAMILY')
    if len(root) <= 3:
        flags.add('SHORT_ROOT')
    if root in {'метр', 'граф', 'авто', 'образ', 'гидр', 'радио', 'фото'} or any(
        root.startswith(prefix) for prefix in ('авто', 'гидр', 'радио', 'фото')
    ):
        flags.add('COMBINING_FORM')
    if root not in members:
        flags.add('NO_STANDALONE_LEMMA')
    hyphenated = sum('-' in member for member in members)
    if hyphenated and hyphenated * 2 >= max(1, len(members)):
        flags.add('COMPOUND_HEAVY')
    if len(members) >= 6:
        flags.add('HIGH_POS_DIVERSITY_OR_FAMILY_SIZE')
    return flags


def _authorization(row):
    evidence = row.get('evidence', '')
    root_text = row.get('tikhonov_roots', '')
    kuz = row.get('kuz_memberships', '')
    flags = set()
    root_count = len([x for x in root_text.split('|') if x])
    kuz_count = len([x for x in kuz.split('|') if x])
    if root_count > 1:
        flags.add('COMPOUND_MULTIPLE_UNRESOLVED')
    elif kuz_count == 1:
        flags.add('SINGLE_ROOT_ZERO_CANDIDATE')
    elif kuz_count > 1:
        flags.add('SINGLE_ROOT_AMBIGUOUS')
    else:
        flags.add('OTHER')
    if root_count == 1 and ('|' not in kuz) and kuz:
        flags.add('KUZ_TIKHONOV_DISAGREEMENT')
    return flags


def _structure_kind(row):
    try:
        structures = json.loads(row.get('current_root_structures') or '{}')
    except json.JSONDecodeError:
        structures = {}
    if any(not value for value in structures.values()):
        return 'UNRESOLVED_COMPONENT'
    # Class E is generated only from equal complete Tikhonov multisets.  Do
    # not reinterpret that as subset matching; preserve the explicit reason.
    if row.get('class') == 'E_TIKHONOV_STRUCTURE_MISMATCH':
        return 'FULL_STRUCTURE_EQUAL'
    return 'OTHER'


def classify(row):
    status = row.get('review_status', '')
    if status in {'SUPPRESSED_BY_REVIEW', 'REVIEWED_CLOSED'}:
        return 'SUPPRESSED', 'explicit reviewed CLOSED decision'
    if row.get('actual') != 'CLOSED':
        return 'SUPPRESSED', 'already OPEN in current matcher'
    if _semantic(row):
        return 'TIER_C_NOISY', 'semantic conflict or split evidence'

    candidate_class = row.get('class', '')
    if candidate_class == 'A_TIKHONOV_ONLY':
        flags = _opaque(row)
        if not flags and _frequency(row) > 0:
            return 'TIER_A_STRONG', 'small standalone Tikhonov-only family, corpus-relevant, no risk flags'
        if not flags:
            return 'TIER_B_PLAUSIBLE', 'clean family but dictionary-only'
        return 'TIER_C_NOISY', 'opaque-family risk flags: ' + '|'.join(sorted(flags))
    if candidate_class == 'B_ROOTLESS_DERIVATION':
        if not row.get('resolved_root'):
            return 'TIER_C_NOISY', 'no unique resolved root identity'
        return 'TIER_B_PLAUSIBLE', 'productive derivation is evidence, not proof'
    if candidate_class == 'C_DICTIONARY_DISAGREEMENT':
        flags = _authorization(row)
        if flags == {'SINGLE_ROOT_ZERO_CANDIDATE'} and _frequency(row) > 0:
            return 'TIER_A_STRONG', 'single-root Kuz/Tikhonov authorization hole with corpus evidence'
        if 'COMPOUND_MULTIPLE_UNRESOLVED' in flags:
            return 'TIER_C_NOISY', 'multiple unresolved compound components'
        return 'TIER_B_PLAUSIBLE', 'dictionary disagreement requires lemma-level review'
    if candidate_class == 'E_TIKHONOV_STRUCTURE_MISMATCH':
        kind = _structure_kind(row)
        if kind == 'FULL_STRUCTURE_EQUAL':
            return 'TIER_A_STRONG', 'equal complete Tikhonov multiset; runtime structure differs'
        return 'TIER_C_NOISY', kind
    return 'TIER_C_NOISY', 'unclassified candidate type'


def enrich(row):
    tier, why = classify(row)
    result = dict(row)
    result['tier'] = tier
    result['tier_reason'] = why
    if row.get('class') == 'A_TIKHONOV_ONLY':
        result['risk_flags'] = '|'.join(sorted(_opaque(row)))
    elif row.get('class') == 'C_DICTIONARY_DISAGREEMENT':
        result['risk_flags'] = '|'.join(sorted(_authorization(row)))
    elif row.get('class') == 'E_TIKHONOV_STRUCTURE_MISMATCH':
        result['structure_kind'] = _structure_kind(row)
    return result


def rank(rows):
    tier_order = {'TIER_A_STRONG': 0, 'TIER_B_PLAUSIBLE': 1, 'TIER_C_NOISY': 2, 'SUPPRESSED': 3}
    return sorted(rows, key=lambda row: (
        tier_order.get(row.get('tier'), 9),
        -_frequency(row),
        row.get('candidate_id', ''),
    ))


def main():
    parser = argparse.ArgumentParser(description='Rank existing Censorly miner artifacts for human review.')
    parser.add_argument('--input-dir', type=Path, default=Path('/tmp/censorly-candidate-miner'))
    parser.add_argument('--output-dir', type=Path, default=Path('/tmp/censorly-candidate-review'))
    parser.add_argument('--sample-size', type=int, default=100)
    args = parser.parse_args()

    source_files = ('opaque_families.tsv', 'derived_candidates.tsv', 'authorization_holes.tsv', 'semantic_conflicts.tsv')
    rows = []
    for name in source_files:
        rows.extend(read_tsv(args.input_dir / name))
    # semantic_conflicts is a filtered report, so deduplicate it against the
    # primary class files rather than treating it as a fifth class.
    unique = {}
    for row in rows:
        unique[row.get('candidate_id', '')] = row
    rows = list(unique.values())
    rows = rank([enrich(row) for row in rows])
    args.output_dir.mkdir(parents=True, exist_ok=True)

    common_fields = [
        'rank', 'candidate_type', 'candidate_id', 'root_spelling', 'lemma', 'family', 'members',
        'resolved_root', 'rule', 'actual', 'why_closed', 'tier', 'tier_reason', 'evidence',
        'risk_flags', 'corpus_frequency', 'review_status', 'suggested_review_layer',
    ]
    def compact(row, rank_number):
        output = {
            'rank': rank_number,
            'candidate_type': row.get('class', ''),
            'candidate_id': row.get('candidate_id', ''),
            'root_spelling': row.get('root_spelling', row.get('candidate_root_spelling', '')),
            'lemma': row.get('lemma', row.get('lemma_a', '')),
            'family': row.get('family', ''),
            'members': row.get('members', ''),
            'resolved_root': row.get('resolved_root', ''),
            'rule': row.get('rule', ''),
            'actual': row.get('actual', ''),
            'why_closed': row.get('tier_reason', ''),
            'tier': row.get('tier', ''),
            'tier_reason': row.get('tier_reason', ''),
            'evidence': row.get('evidence', ''),
            'risk_flags': row.get('risk_flags', ''),
            'corpus_frequency': row.get('corpus_frequency', '0'),
            'review_status': row.get('review_status', ''),
            'suggested_review_layer': 'REVIEWED_OPAQUE_ROOT' if row.get('class') == 'A_TIKHONOV_ONLY' else (
                'REVIEWED_DERIVED_ROOT' if row.get('class') == 'B_ROOTLESS_DERIVATION' else 'REVIEW_REQUIRED'
            ),
        }
        return output

    pending = [row for row in rows if row['tier'] != 'SUPPRESSED']
    tier_a = [row for row in pending if row['tier'] == 'TIER_A_STRONG']
    tier_b = [row for row in pending if row['tier'] == 'TIER_B_PLAUSIBLE']
    write_tsv(args.output_dir / 'top_tier_a.tsv', [compact(row, i) for i, row in enumerate(tier_a, 1)], common_fields)
    write_tsv(args.output_dir / 'top_tier_b.tsv', [compact(row, i) for i, row in enumerate(tier_b, 1)], common_fields)
    derived_rows = [row for row in rows if row.get('class') == 'B_ROOTLESS_DERIVATION']
    write_tsv(args.output_dir / 'derived_review.tsv', [compact(row, i) for i, row in enumerate(derived_rows, 1)], common_fields)

    audit_rows = []
    for i, row in enumerate(tier_a[:args.sample_size], 1):
        item = compact(row, i)
        item['human_label'] = ''
        item['human_note'] = ''
        audit_rows.append(item)
    write_tsv(args.output_dir / 'manual_precision_audit.tsv', audit_rows, common_fields + ['human_label', 'human_note'])

    counts = Counter(row['tier'] for row in rows)
    class_counts = defaultdict(Counter)
    for row in rows:
        class_counts[row.get('class', '')][row['tier']] += 1
    summary = {
        'source_rows': len(rows),
        'pending_total': len(pending),
        'tiers': dict(counts),
        'by_class': {key: dict(value) for key, value in sorted(class_counts.items())},
        'tier_a': len(tier_a),
        'manual_audit_rows': min(args.sample_size, len(tier_a)),
        'manual_precision': 'NOT_MEASURED_HUMAN_LABELS_REQUIRED',
        'artifacts': ['top_tier_a.tsv', 'top_tier_b.tsv', 'derived_review.tsv', 'manual_precision_audit.tsv'],
    }
    (args.output_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for row in tier_a[:50]:
        print(f"{row.get('candidate_id')} | {row.get('class')} | {row.get('root_spelling', row.get('lemma', ''))} | {row.get('members', row.get('family', ''))} | corpus={row.get('corpus_frequency', '0')} | {row.get('tier_reason')}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
