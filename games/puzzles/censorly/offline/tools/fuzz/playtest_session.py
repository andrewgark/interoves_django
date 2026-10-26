"""Offline playtest-session report for Censorly.

This tool only diagnoses observed pairs.  It never changes the review queue,
canonical assignments, or resolver configuration.  Use ``review_pair`` for
the explicit human-review step after inspecting this report.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django

django.setup()

from games.puzzles.censorly.offline.tools.review.explain_pair import explain_pair

FIELDS = (
    'guess', 'target', 'article', 'expected', 'actual', 'classification',
    'provenance', 'suggested_fix_layer', 'review_status', 'note',
)


def _actual(report):
    return 'OPEN' if report['runtime_reason'] != 'CLOSED' else 'CLOSED'


def _provenance(report):
    layers = report['layers']
    reasons = []
    if layers['EXACT']:
        reasons.append('EXACT')
    if layers['SAME_LEXEME']:
        reasons.append('SAME_LEXEME')
    if layers['ROOT_SET_EQUALITY']:
        reasons.append('ROOT_SET_EQUALITY')
    if layers['IRREGULAR_GRAMMAR']:
        reasons.append('GRAMMAR')
    if layers['ALIAS']:
        reasons.append('ALIAS')
    if layers['PROPER_PAIR']:
        reasons.append('PROPER_PAIR')
    if layers['SERVICE_WORD']:
        reasons.append('SERVICE_WORD')
    if not reasons:
        return 'CLOSED'
    return '|'.join(reasons)


def _classification(report, expected, actual):
    if expected == actual:
        return 'MATCH'
    if expected == 'OPEN':
        return report['classification']
    return {
        'EXACT': 'EXACT_OPENING',
        'LEXEME': 'SAME_LEXEME_OPENING',
        'ROOT': 'ROOT_EQUALITY_OPENING',
        'GRAMMAR': 'GRAMMAR_OPENING',
        'ALIAS': 'ALIAS_OPENING',
        'PROPER': 'PROPER_OPENING',
    }.get(report['runtime_reason'].upper(), 'SERVICE_WORD_OPENING' if report['layers']['SERVICE_WORD'] else 'OTHER_OPENING')


def _read(path):
    with path.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream, delimiter='\t')
        required = {'guess', 'target', 'expected'}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f'{path}: missing columns: {", ".join(sorted(missing))}')
        return list(reader)


def _rows(cases):
    result = []
    for case in cases:
        guess = (case.get('guess') or '').strip()
        target = (case.get('target') or '').strip()
        expected = (case.get('expected') or '').strip().upper()
        if not guess or not target or expected not in {'OPEN', 'CLOSED'}:
            raise ValueError(f'invalid case: {case!r}')
        report = explain_pair(guess, target)
        actual = _actual(report)
        result.append({
            'guess': guess,
            'target': target,
            'article': (case.get('article') or '').strip(),
            'expected': expected,
            'actual': actual,
            'classification': _classification(report, expected, actual),
            'provenance': _provenance(report),
            'suggested_fix_layer': report['recommendation'],
            'review_status': (case.get('review_status') or 'NEW').strip() or 'NEW',
            'note': (case.get('note') or '').strip(),
        })
    return result


def _write(path, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter='\t', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def _print_summary(rows):
    print(f'articles played: {len({row["article"] for row in rows if row["article"]})}')
    print(f'guesses tested: {len(rows)}')
    print(f'false negatives: {sum(row["expected"] == "OPEN" and row["actual"] == "CLOSED" for row in rows)}')
    print(f'false positives: {sum(row["expected"] == "CLOSED" and row["actual"] == "OPEN" for row in rows)}')
    print(f'unclear cases: {sum(row["classification"] in {"G/H", "J", "UNKNOWN"} for row in rows)}')
    print('by classification: ' + repr(dict(Counter(row['classification'] for row in rows))))
    print('by provenance: ' + repr(dict(Counter(row['provenance'] for row in rows))))
    print('by suggested fix: ' + repr(dict(Counter(row['suggested_fix_layer'] for row in rows))))


def main():
    parser = argparse.ArgumentParser(description='Build an offline Censorly manual-playtest report.')
    parser.add_argument('cases_file', type=Path, help='TSV: guess, target, expected, optional article/note')
    parser.add_argument('--output', type=Path, help='write detailed TSV report here')
    args = parser.parse_args()
    rows = _rows(_read(args.cases_file))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        _write(args.output, rows)
        print(f'report: {args.output}')
    print('guess\ttarget\tarticle\texpected\tactual\tclassification\tprovenance\tsuggested_fix_layer\treview_status')
    for row in rows:
        print('\t'.join(row[field] for field in FIELDS[:-1]))
    print('\nSESSION SUMMARY')
    _print_summary(rows)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
