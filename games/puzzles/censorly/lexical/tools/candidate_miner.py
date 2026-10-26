"""Offline false-negative candidate miner for Censorly.

This tool produces advisory reports only.  It never edits review queues,
canonical assignments, semantic splits, or runtime code.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
django.setup()

from games.censorly.lexical.core import fold
from games.censorly.lexical.match import _GuessPack, _Pack, _relation
from games.censorly.lexical.prelaunch import iter_fixtures
from games.censorly.lexical.rootbank import (
    _KUZ_GROUPS, _KUZ_LEMMAS, _TIKHONOV, _families, _game_id,
    _SENSE, _lines, _norm, _plain, _strip_note, assignment_of, families_of,
    structures_of, REVIEWED_OPAQUE_ROOTS, REVIEWED_DERIVED_ROOTS,
)
from games.censorly.lexical.russian.compiler import _families as compiler_families
from games.censorly.tokenize import tokenize_text

SUFFIXES = (
    ('-овый', 'adjective:-овый'),
    ('-евый', 'adjective:-евый'),
    ('-иный', 'adjective:-иный'),
    ('-ский', 'adjective:-ский'),
    ('-ной', 'adjective:-ной'),
    ('-ный', 'adjective:-ный'),
    ('-ник', 'person/thing:-ник'),
    ('-чик', 'person/thing:-чик'),
    ('-щик', 'person/thing:-щик'),
    ('-ость', 'abstract:-ость'),
    ('-ение', 'nominal:-ение'),
    ('-ание', 'nominal:-ание'),
    ('-ство', 'nominal:-ство'),
    ('-ка', 'diminutive:-ка'),
    ('-ок', 'diminutive:-ок'),
    ('-ик', 'diminutive:-ик'),
)
COMBINING_FLAGS = ('авто', 'гидр', 'радио', 'фото')
RISKY_SHORT = {'мир', 'мор', 'граф', 'метр', 'образ', 'мед', 'свет', 'год', 'мать'}
REVIEW_STATUSES = {'APPROVED', 'APPLIED', 'CONFIRMED', 'REJECTED', 'KEEP_CLOSED'}


def _read_tsv(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def _review_knowledge(review_file):
    rows = _read_tsv(review_file)
    reviewed_pairs = set()
    suppressed_pairs = set()
    suppressed_lemmas = set()
    applied_lemmas = set(REVIEWED_DERIVED_ROOTS)
    for row in rows:
        key = tuple(sorted((fold(row['guess']), fold(row['target']))))
        reviewed_pairs.add(key)
        if row.get('status') in {'CONFIRMED', 'REJECTED', 'KEEP_CLOSED'}:
            suppressed_pairs.add(key)
            suppressed_lemmas.update(key)
        if row.get('status') in {'APPROVED', 'APPLIED'}:
            applied_lemmas.update((fold(row['guess']), fold(row['target'])))
    return rows, reviewed_pairs, suppressed_pairs, suppressed_lemmas, applied_lemmas


def _tikhonov():
    by_root = defaultdict(set)
    by_lemma = {}
    for line in _lines(_TIKHONOV):
        raw, decomposition = line.split('\t', 1)
        lemma = fold(_strip_note(raw))
        roots = []
        for part in decomposition.split('/'):
            if ':' not in part:
                continue
            morph, kind = part.rsplit(':', 1)
            if kind == 'ROOT':
                spelling = _plain(morph)
                if spelling:
                    roots.append(spelling)
        if lemma and roots:
            by_lemma[lemma] = tuple(roots)
            for root in set(roots):
                by_root[root].add(lemma)
    return by_root, by_lemma


def _root_spelling_ids():
    families = compiler_families(_KUZ_GROUPS)
    result = {}
    for morph, family in families.items():
        plain = _plain(morph)
        if plain:
            result.setdefault(plain, set()).add(_game_id(morph, family))
    return result


def _corpus_evidence():
    counts = Counter()
    lemmas = defaultdict(set)
    surfaces = set()
    tokens = 0
    for _article, _kind, body in iter_fixtures():
        for token in tokenize_text(body):
            tokens += 1
            if token.get('kind') != 'content':
                continue
            surface = token.get('surface') or ''
            if not surface:
                continue
            normalized = fold(surface)
            surfaces.add(normalized)
            counts[normalized] += 1
            # Tokenizer already carries its stored lemma.  Reusing it keeps
            # corpus relevance one-pass and avoids morphology calls for every
            # unrelated fixture surface.
            lemma = fold(token.get('lemma') or '')
            if lemma:
                lemmas[lemma].add(normalized)
    return tokens, counts, lemmas, surfaces


def _suffix_candidate(lemma):
    for suffix, subtype in SUFFIXES:
        ending = suffix[1:]
        if lemma.endswith(ending) and len(lemma) > len(ending) + 1:
            return lemma[:-len(ending)], suffix, subtype
    return None


def _corpus_count(lemma, corpus_lemmas):
    return len(corpus_lemmas.get(lemma, ()))


def _pair_suppressed(a, b, suppressed):
    return tuple(sorted((fold(a), fold(b)))) in suppressed


def _matcher_probe(pairs, pack_cache):
    """Run the real matcher on a bounded evidence-derived pair set."""
    reasons = []
    for guess, target in pairs:
        gp = pack_cache.setdefault(('g', guess), _GuessPack(guess))
        tp = pack_cache.setdefault(('t', target), _Pack(target))
        reason = _relation(gp, tp)
        reasons.append({'guess': guess, 'target': target, 'reason': reason or 'CLOSED'})
    if any(item['reason'] not in {'CLOSED', ''} for item in reasons):
        status = 'ALREADY_OPEN'
    else:
        status = 'CLOSED'
    return status, reasons


def _family_row(root, members, root_ids, bank, corpus_lemmas, suppressed, pack_cache, root_reps):
    members = sorted(members)
    structures = {lemma: structures_of(lemma) for lemma in members}
    has_standalone = root in members
    conflicts = sorted(
        name for lemma in members for name in _SENSE.get(lemma, ())
    )
    flags = []
    if len(members) > 8:
        flags.append('large_family')
    if root in COMBINING_FLAGS or any(root.startswith(prefix) for prefix in COMBINING_FLAGS):
        flags.append('combining_form')
    if root in RISKY_SHORT or len(root) <= 3:
        flags.append('short_ambiguous')
    if conflicts:
        flags.append('semantic_conflict')
    if any(_pair_suppressed(a, b, suppressed) for index, a in enumerate(members) for b in members[index + 1:]):
        flags.append('reviewed_closed')
    corpus_occurrences = sum(_corpus_count(member, corpus_lemmas) for member in members)
    probe_pairs = []
    if has_standalone:
        probe_pairs = [(root, member) for member in members if member != root]
    elif len(members) >= 2:
        probe_pairs = [(members[0], members[1])]
    actual, matcher_checks = _matcher_probe(probe_pairs, pack_cache) if probe_pairs else ('CLOSED', [])
    risky = set(flags) - {'semantic_conflict'}
    priority = 'P0' if corpus_occurrences and has_standalone and len(members) <= 8 and not flags else (
        'P1' if corpus_occurrences and not flags else ('P3' if conflicts or flags else 'P2')
    )
    return {
        'candidate_id': f'opaque:{root}',
        'class': 'A_TIKHONOV_ONLY',
        'root_spelling': root,
        'members': '|'.join(members),
        'root_ids': '|'.join(sorted(root_ids)),
        'actual': actual,
        'matcher_checks': json.dumps(matcher_checks, ensure_ascii=False, sort_keys=True),
        'evidence': 'same Tikhonov ROOT spelling',
        'tikhonov_roots': root,
        'current_root_structures': json.dumps(structures, ensure_ascii=False, sort_keys=True),
        'semantic_conflicts': '|'.join(conflicts),
        'corpus_frequency': str(corpus_occurrences),
        'review_status': 'SUPPRESSED_BY_REVIEW' if 'reviewed_closed' in flags else ('PENDING' if actual == 'CLOSED' else 'ALREADY_OPEN'),
        'priority': priority,
        'risk_flags': '|'.join(flags),
    }


def mine(args):
    started = time.perf_counter()
    bank, _ = __import__('games.censorly.lexical.rootbank', fromlist=['_bank'])._bank()
    review_rows, reviewed_pairs, suppressed, suppressed_lemmas, applied_lemmas = _review_knowledge(args.review_file)
    corpus_tokens, corpus_counts, corpus_lemmas, corpus_surfaces = _corpus_evidence()
    by_root, by_lemma = _tikhonov()
    root_ids = _root_spelling_ids()
    pack_cache = {}
    root_reps = {}
    for lemma, structures in bank.items():
        for structure in structures:
            for root_id in structure:
                root_reps.setdefault(root_id, lemma)

    opaque_rows = []
    for root, members in sorted(by_root.items()):
        if root in REVIEWED_OPAQUE_ROOTS or len(members) < 2:
            continue
        ids = root_ids.get(root, set())
        if ids:
            # A Kuz-known spelling is a disagreement candidate, not an opaque
            # zero-Kuz candidate; it is retained in authorization_holes.
            continue
        opaque_rows.append(_family_row(root, members, ids, bank, corpus_lemmas, suppressed, pack_cache, root_reps))

    authorization = []
    for root, members in sorted(by_root.items()):
        ids = root_ids.get(root, set())
        if not ids:
            continue
        for lemma in sorted(members):
            structures = structures_of(lemma)
            if not structures:
                authorization.append({
                    'candidate_id': f'authorization:{root}:{lemma}',
                    'class': 'C_DICTIONARY_DISAGREEMENT',
                    'lemma_a': lemma,
                    'lemma_b': '',
                    'actual': 'CLOSED',
                    'evidence': f'Tikhonov ROOT={root}; Kuz/root family={"|".join(sorted(ids))}; rootbank structure empty',
                    'tikhonov_roots': root,
                    'kuz_memberships': '|'.join(families_of(lemma)),
                    'current_root_structures': '',
                    'semantic_conflicts': '|'.join(_SENSE.get(lemma, ())),
                    'corpus_frequency': str(_corpus_count(lemma, corpus_lemmas)),
                    'review_status': 'PENDING',
                    'priority': 'P1' if _corpus_count(lemma, corpus_lemmas) else 'P2',
                    'risk_flags': 'semantic_conflict' if _SENSE.get(lemma) else '',
                })
                if lemma in suppressed_lemmas:
                    authorization[-1]['review_status'] = 'SUPPRESSED_BY_REVIEW'
                    authorization[-1]['priority'] = 'P3'
                    authorization[-1]['risk_flags'] = 'reviewed_closed'
                representative = next((root_reps.get(root_id) for root_id in ids if root_reps.get(root_id)), None)
                if representative:
                    actual, checks = _matcher_probe([(lemma, representative)], pack_cache)
                    authorization[-1]['actual'] = actual
                    authorization[-1]['matcher_checks'] = json.dumps(checks, ensure_ascii=False, sort_keys=True)

    derived_rows = []
    for lemma in sorted(set(bank) | set(corpus_lemmas)):
        if lemma in REVIEWED_DERIVED_ROOTS or structures_of(lemma):
            continue
        candidate = _suffix_candidate(lemma)
        if not candidate:
            continue
        base, suffix, subtype = candidate
        matches = root_ids.get(base, set())
        if len(matches) != 1:
            continue
        root_id = next(iter(matches))
        conflicts = _SENSE.get(lemma, ())
        derived_rows.append({
            'candidate_id': f'derived:{lemma}:{root_id}',
            'class': 'B_ROOTLESS_DERIVATION',
            'derivation_class': 'D_PRODUCTIVE_DERIVATION',
            'lemma': lemma,
            'candidate_root_spelling': base,
            'resolved_root': root_id,
            'rule': subtype,
            'actual': 'CLOSED',
            'evidence': f'{lemma} -> {base} by {subtype}; exact root spelling maps uniquely',
            'tikhonov_roots': '|'.join(by_lemma.get(lemma, ())),
            'current_root_structures': json.dumps(structures_of(lemma), ensure_ascii=False),
            'semantic_conflicts': '|'.join(conflicts),
            'corpus_frequency': str(_corpus_count(lemma, corpus_lemmas)),
            'review_status': 'SEMANTIC_CONFLICT' if conflicts else 'PENDING',
            'priority': 'P1' if _corpus_count(lemma, corpus_lemmas) and not conflicts else ('P3' if conflicts else 'P2'),
            'risk_flags': 'semantic_conflict' if conflicts else '',
        })
        if lemma in suppressed_lemmas:
            derived_rows[-1]['review_status'] = 'SUPPRESSED_BY_REVIEW'
            derived_rows[-1]['priority'] = 'P3'
            derived_rows[-1]['risk_flags'] = 'reviewed_closed'
        representative = root_reps.get(root_id)
        if representative:
            actual, checks = _matcher_probe([(lemma, representative)], pack_cache)
            derived_rows[-1]['actual'] = actual
            derived_rows[-1]['matcher_checks'] = json.dumps(checks, ensure_ascii=False, sort_keys=True)

    # A same-full-Tikhonov-structure candidate is retained separately; this
    # uses complete multisets, never component/subset overlap.
    by_structure = defaultdict(list)
    for lemma, roots in by_lemma.items():
        by_structure[tuple(sorted(roots))].append(lemma)
    structural_rows = []
    for structure, members in sorted(by_structure.items()):
        if len(members) < 2:
            continue
        runtime = {lemma: structures_of(lemma) for lemma in members}
        if len({json.dumps(value, sort_keys=True) for value in runtime.values()}) == 1:
            continue
        probe_pairs = []
        if len(members) >= 2:
            probe_pairs = [(members[0], members[1])]
        actual, checks = _matcher_probe(probe_pairs, pack_cache) if probe_pairs else ('CLOSED', [])
        structural_rows.append({
            'candidate_id': 'structure:' + '+'.join(structure),
            'class': 'E_TIKHONOV_STRUCTURE_MISMATCH',
            'family': '|'.join(sorted(members)),
            'actual': actual,
            'matcher_checks': json.dumps(checks, ensure_ascii=False, sort_keys=True),
            'evidence': 'equal complete Tikhonov root multiset; runtime structures differ',
            'tikhonov_roots': '+'.join(structure),
            'current_root_structures': json.dumps(runtime, ensure_ascii=False, sort_keys=True),
            'semantic_conflicts': '|'.join(sorted({s for lemma in members for s in _SENSE.get(lemma, ())})),
            'corpus_frequency': str(sum(_corpus_count(lemma, corpus_lemmas) for lemma in members)),
            'review_status': 'PENDING',
            'priority': 'P1' if any(lemma in corpus_lemmas for lemma in members) else 'P2',
            'risk_flags': '',
        })

    for row in opaque_rows + derived_rows + authorization + structural_rows:
        if row.get('actual') == 'ALREADY_OPEN':
            row['review_status'] = 'ALREADY_OPEN'
        if row.get('risk_flags', '').find('semantic_conflict') >= 0:
            row['priority'] = 'P3'

    return {
        'opaque': opaque_rows,
        'derived': derived_rows,
        'authorization': authorization,
        'structural': structural_rows,
        'corpus_tokens': corpus_tokens,
        'corpus_surfaces': len(corpus_surfaces),
        'elapsed_seconds': round(time.perf_counter() - started, 3),
        'reviewed_rows': len(review_rows),
        'applied_lemmas': len(applied_lemmas),
        'matcher_checks': sum(
            len(json.loads(row.get('matcher_checks', '[]')))
            for rows in (opaque_rows, derived_rows, authorization, structural_rows)
            for row in rows
        ),
        'pack_cache_size': len(pack_cache),
        'reviewed_opaque_excluded': len(REVIEWED_OPAQUE_ROOTS),
        'reviewed_derived_excluded': len(REVIEWED_DERIVED_ROOTS),
    }


def write_tsv(path, rows):
    if not rows:
        path.write_text('', encoding='utf-8')
        return
    fields = list(rows[0])
    for row in rows[1:]:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter='\t', lineterminator='\n')
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: (row.get('priority', ''), row.get('candidate_id', ''))))


def _all_rows(result):
    return result['opaque'] + result['derived'] + result['authorization'] + result['structural']


def _manual_sample(rows, limit=30):
    """Deterministic, stratified sample for human precision review."""
    grouped = defaultdict(list)
    for row in rows:
        if row.get('actual') == 'CLOSED' and row.get('review_status') == 'PENDING':
            grouped[row.get('class', '')].append(row)
    sample = []
    classes = sorted(grouped)
    quota = max(1, limit // max(1, len(classes)))
    for class_name in classes:
        ordered = sorted(
            grouped[class_name],
            key=lambda row: (-int(row.get('corpus_frequency', 0) or 0), row.get('candidate_id', '')),
        )
        sample.extend(ordered[:quota])
    if len(sample) < limit:
        seen = {row.get('candidate_id') for row in sample}
        remaining = sorted(
            (row for row in rows if row.get('actual') == 'CLOSED' and row.get('review_status') == 'PENDING'
             and row.get('candidate_id') not in seen),
            key=lambda row: (-int(row.get('corpus_frequency', 0) or 0), row.get('candidate_id', '')),
        )
        sample.extend(remaining[:limit - len(sample)])
    return sample[:limit]


def main():
    parser = argparse.ArgumentParser(description='Mine advisory Censorly false-negative candidates.')
    parser.add_argument('--review-file', type=Path, default=Path(__file__).resolve().parents[1] / 'data' / 'pair_review.tsv')
    parser.add_argument('--output-dir', type=Path, default=Path('/tmp/censorly-candidate-miner'))
    parser.add_argument('--export-review-candidates', type=Path)
    args = parser.parse_args()
    result = mine(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_tsv(args.output_dir / 'opaque_families.tsv', result['opaque'])
    write_tsv(args.output_dir / 'derived_candidates.tsv', result['derived'])
    write_tsv(args.output_dir / 'authorization_holes.tsv', result['authorization'])
    write_tsv(args.output_dir / 'semantic_conflicts.tsv', [row for row in result['derived'] + result['authorization'] + result['structural'] if row.get('semantic_conflicts')])
    write_tsv(args.output_dir / 'corpus_priority.tsv', sorted(result['opaque'] + result['derived'] + result['authorization'] + result['structural'], key=lambda row: (-int(row.get('corpus_frequency', 0)), row.get('candidate_id', ''))))
    clean_opaque = sorted([
        row for row in result['opaque']
        if not row.get('risk_flags') and row.get('actual') == 'CLOSED'
    ], key=lambda row: (-int(row.get('corpus_frequency', 0) or 0), row.get('candidate_id', '')))
    write_tsv(args.output_dir / 'opaque_top100.tsv', clean_opaque[:100])
    write_tsv(args.output_dir / 'manual_sample.tsv', _manual_sample(_all_rows(result)))
    all_rows = _all_rows(result)
    summary = {
        'raw_candidates': len(all_rows),
        'by_class': {key: len(result[key]) for key in ('opaque', 'derived', 'authorization', 'structural')},
        'already_reviewed': result['reviewed_opaque_excluded'] + result['reviewed_derived_excluded'],
        'suppressed_by_review': sum(row.get('review_status') == 'SUPPRESSED_BY_REVIEW' for row in all_rows),
        'already_open': sum(row.get('review_status') == 'ALREADY_OPEN' for row in all_rows),
        'semantic_conflicts': sum(bool(row.get('semantic_conflicts')) for row in all_rows),
        'pending_review': sum(row.get('review_status') == 'PENDING' and row.get('actual') == 'CLOSED' for row in all_rows),
        'corpus_tokens': result['corpus_tokens'],
        'corpus_surfaces': result['corpus_surfaces'],
        'reviewed_rows': result['reviewed_rows'],
        'elapsed_seconds': result['elapsed_seconds'],
        'matcher_checks': result['matcher_checks'],
        'pack_cache_size': result['pack_cache_size'],
        'artifacts': ['summary.json', 'opaque_families.tsv', 'opaque_top100.tsv', 'derived_candidates.tsv', 'authorization_holes.tsv', 'semantic_conflicts.tsv', 'corpus_priority.tsv', 'manual_sample.tsv'],
    }
    (args.output_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.export_review_candidates:
        fields = ('class', 'candidate_id', 'lemma_a', 'lemma_b', 'lemma', 'family', 'root_spelling', 'resolved_root', 'rule', 'actual', 'evidence', 'priority', 'review_status', 'note')
        with args.export_review_candidates.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter='\t', extrasaction='ignore', lineterminator='\n')
            writer.writeheader()
            for row in all_rows:
                if row.get('actual') != 'CLOSED' or row.get('review_status') != 'PENDING':
                    continue
                writer.writerow(row)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

