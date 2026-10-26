"""Human review queue for Censorly pair diagnostics."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import tempfile
from collections import Counter
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django

django.setup()

from games.models import CensorlyReviewCase
from games.puzzles.censorly.lexical.match.core import fold
from games.puzzles.censorly.offline.tools.review.explain_pair import explain_pair

DATA = Path(__file__).resolve().parents[1] / 'data'
REVIEW_FILE = Path(os.environ.get('CENSORLY_PAIR_REVIEW_FILE', DATA / 'pair_review.tsv'))
HISTORY_FILE = Path(os.environ.get('CENSORLY_PAIR_REVIEW_HISTORY_FILE', DATA / 'pair_review_history.tsv'))
FIELDS = ('guess', 'target', 'expected', 'actual', 'classification', 'decision', 'fix_layer', 'status', 'note')
HISTORY_FIELDS = ('updated_at', 'guess', 'target', 'previous_status', 'status', 'decision', 'note')
DECISIONS = {'APPROVE', 'REJECT', 'KEEP_CLOSED', 'KEEP_OPEN', 'NEEDS_MORE_DATA'}
STATUSES = {'NEW', 'DIAGNOSED', 'NEEDS_REVIEW', 'APPROVED', 'REJECTED', 'APPLIED', 'REGRESSION_ADDED', 'CONFIRMED'}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def _read():
    if not REVIEW_FILE.exists():
        return []
    with REVIEW_FILE.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def _write(rows):
    REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix='{}.tmp.'.format(REVIEW_FILE.name),
        dir=str(REVIEW_FILE.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter='\t', lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, REVIEW_FILE)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _history(row, previous_status, status, note):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    exists = HISTORY_FILE.exists()
    with HISTORY_FILE.open('a', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=HISTORY_FIELDS, delimiter='\t', lineterminator='\n')
        if not exists:
            writer.writeheader()
        writer.writerow({
            'updated_at': _now(),
            'guess': row['guess'],
            'target': row['target'],
            'previous_status': previous_status,
            'status': status,
            'decision': row.get('decision', ''),
            'note': note,
        })


def _actual(report):
    return 'OPEN' if report['runtime_reason'] != 'CLOSED' else 'CLOSED'


def _pair_key(guess, target, symmetric=True):
    left, right = fold(guess), fold(target)
    return tuple(sorted((left, right))) if symmetric else (left, right)


def _pair_key_text(guess, target):
    return '\x1f'.join(_pair_key(guess, target))


def _record_durable_case(guess, target, expected, *, actual, classification, fix_layer, note):
    from django.db import transaction
    from django.db.models import F

    pair_key = _pair_key_text(guess, target)
    with transaction.atomic():
        case, created = CensorlyReviewCase.objects.get_or_create(
            pair_key=pair_key,
            expected=expected,
            defaults={
                'guess': guess,
                'target': target,
                'actual': actual,
                'classification': classification,
                'fix_layer': fix_layer,
                'note': note,
                'report_count': 1,
            },
        )
        if not created:
            updates = {'report_count': F('report_count') + 1}
            if note and note not in case.note:
                updates['note'] = '{} | {}'.format(case.note, note).strip(' |')
            CensorlyReviewCase.objects.filter(pk=case.pk).update(**updates)
            case.refresh_from_db()
    return case


def _find(rows, guess, target):
    direct = _pair_key(guess, target, False)
    reverse = _pair_key(target, guess, False)
    for row in rows:
        key = _pair_key(row['guess'], row['target'], False)
        if key == direct:
            return row
        if key == reverse:
            try:
                reverse_report = explain_pair(target, guess)
                if (
                    _actual(reverse_report) == row['actual']
                    and row['actual'] == ('OPEN' if row['expected'] == 'OPEN' else row['actual'])
                ):
                    return row
            except Exception:
                pass
    return None


def record_review_case(guess, target, expected, *, note='', source=''):
    """Create or reuse a diagnosed case for an explicit human review source.

    This is intentionally the same queue/lifecycle used by the CLI.  It never
    approves, applies, or changes resolver data.
    """
    expected = (expected or '').strip().upper()
    if expected not in {'OPEN', 'CLOSED'}:
        raise ValueError('expected must be OPEN or CLOSED')
    rows = _read()
    detail = ' '.join(part for part in (source, note) if part).strip()
    existing = next((row for row in rows if (
        row.get('expected') == expected
        and _pair_key(row.get('guess', ''), row.get('target', '')) == _pair_key(guess, target)
    )), None)
    if existing is not None:
        previous = existing['status']
        if detail and detail not in existing.get('note', ''):
            existing['note'] = '{} | {}'.format(existing.get('note', ''), detail).strip(' |')
            _history(existing, previous, previous, existing['note'])
            _write(rows)
        case = _record_durable_case(
            existing['guess'], existing['target'], expected,
            actual=existing.get('actual', ''),
            classification=existing.get('classification', ''),
            fix_layer=existing.get('fix_layer', ''),
            note=detail,
        )
        return existing, False, None, case

    report = explain_pair(guess, target)
    actual = _actual(report)
    classification = report['classification']
    fix_layer = _fix_layer(report)

    row = {
        'guess': guess,
        'target': target,
        'expected': expected,
        'actual': actual,
        'classification': classification,
        'decision': '',
        'fix_layer': fix_layer,
        'status': 'DIAGNOSED',
        'note': detail or report['recommendation'],
    }
    rows.append(row)
    _write(rows)
    _history(row, '', row['status'], row['note'])
    case = _record_durable_case(
        guess, target, expected,
        actual=actual,
        classification=classification,
        fix_layer=fix_layer,
        note=row['note'],
    )
    return row, True, report, case


def _decision_status(decision):
    return {
        'APPROVE': 'APPROVED',
        'REJECT': 'REJECTED',
        'KEEP_CLOSED': 'CONFIRMED',
        'KEEP_OPEN': 'CONFIRMED',
        'NEEDS_MORE_DATA': 'NEEDS_REVIEW',
    }[decision]


def _fix_layer(report):
    suggestion = report['recommendation']
    if 'REVIEWED_OPAQUE_ROOT' in suggestion:
        return 'REVIEWED_OPAQUE_ROOT'
    if 'REVIEWED_DERIVED_ROOT' in suggestion:
        return 'REVIEWED_DERIVED_ROOT'
    if 'GRAMMAR' in suggestion:
        return 'IRREGULAR_GRAMMAR'
    if 'split' in suggestion:
        return 'SEMANTIC_SPLIT'
    if 'alias' in suggestion.lower():
        return 'ALIAS'
    return 'NONE'


def _summary(rows):
    counters = {
        'total': len(rows),
        'expected_open': sum(row['expected'] == 'OPEN' for row in rows),
        'expected_closed': sum(row['expected'] == 'CLOSED' for row in rows),
    }
    for key, values in (
        ('classification', (row['classification'] for row in rows)),
        ('fix_layer', (row['fix_layer'] for row in rows)),
        ('status', (row['status'] for row in rows)),
    ):
        counters[key] = dict(Counter(values))
    return counters


def main():
    parser = argparse.ArgumentParser(description='Review queue for Censorly pair diagnostics.')
    parser.add_argument('guess', nargs='?')
    parser.add_argument('target', nargs='?')
    parser.add_argument('--expected', choices=('OPEN', 'CLOSED'))
    parser.add_argument('--decision', choices=sorted(DECISIONS))
    parser.add_argument('--note', default='')
    parser.add_argument('--pending', action='store_true')
    parser.add_argument('--summary', action='store_true')
    args = parser.parse_args()

    rows = _read()
    if args.pending:
        print('guess\ttarget\texpected\tactual\tclassification\tsuggested_fix\tstatus')
        for row in rows:
            if row['status'] in {'NEW', 'DIAGNOSED', 'NEEDS_REVIEW'}:
                print('\t'.join((row['guess'], row['target'], row['expected'], row['actual'], row['classification'], row['fix_layer'], row['status'])))
        return 0
    if args.summary:
        print(_summary(rows))
        return 0
    if not args.guess or not args.target or not args.expected:
        parser.error('provide GUESS TARGET --expected OPEN|CLOSED, or --pending/--summary')

    report = explain_pair(args.guess, args.target)
    actual = _actual(report)
    classification = report['classification']
    fix_layer = _fix_layer(report)
    existing = _find(rows, args.guess, args.target)
    if existing is not None:
        if not args.decision:
            print('DUPLICATE: existing review case')
            print('\t'.join(existing.get(field, '') for field in FIELDS))
            return 0
        previous = existing['status']
        existing['decision'] = args.decision
        existing['status'] = _decision_status(args.decision)
        if args.note:
            existing['note'] = args.note
        _history(existing, previous, existing['status'], existing['note'])
        _write(rows)
        print(f'UPDATED {existing["guess"]}/{existing["target"]}: {previous} -> {existing["status"]}')
        return 0

    status = _decision_status(args.decision) if args.decision else 'DIAGNOSED'
    row = {
        'guess': args.guess,
        'target': args.target,
        'expected': args.expected,
        'actual': actual,
        'classification': classification,
        'decision': args.decision or '',
        'fix_layer': fix_layer,
        'status': status,
        'note': args.note or report['recommendation'],
    }
    rows.append(row)
    _write(rows)
    _history(row, '', status, row['note'])
    print(f'ADDED {args.guess}/{args.target}: actual={actual} classification={classification} fix_layer={fix_layer} status={status}')
    print(f'RECOMMENDATION: {report["recommendation"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

