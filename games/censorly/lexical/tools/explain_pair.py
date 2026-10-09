"""Explain one Censorly lexical pair without changing resolver semantics."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
django.setup()

from games.censorly.lexical.core import fold
from games.censorly.lexical.match import _GuessPack, _Pack, _relation
from games.censorly.lexical.rootbank import (
    _KUZ_LEMMAS, _TIKHONOV, _lines, _plain, _strip_note,
    assignment_of, families_of, structures_of,
)
from games.censorly.lexical.semantics import (
    _ALIAS_GROUPS, _GRAMMAR_GROUPS, _alias, _grammar,
    _proper_pair, _roots, explain, initially_open, readings_of,
)
from games.censorly.lexical.rootbank import REVIEWED_DERIVED_ROOTS, REVIEWED_OPAQUE_ROOTS


def _tikhonov_roots(lemma):
    wanted = fold(lemma)
    result = []
    for line in _lines(_TIKHONOV):
        raw, decomposition = line.split('\t', 1)
        if fold(_strip_note(raw)) != wanted:
            continue
        roots = []
        for part in decomposition.split('/'):
            if ':' in part and part.rsplit(':', 1)[1] == 'ROOT':
                root = _plain(part.rsplit(':', 1)[0])
                if root:
                    roots.append(root)
        result.extend(roots)
    return tuple(result)


def _reading_dict(item):
    return {
        'lemma': item.lemma,
        'pos': item.pos,
        'score': item.score,
        'para_id': item.para_id,
        'lexical_identity': (item.para_id, item.lemma),
        'kuznetsova_memberships': families_of(item.lemma),
        'tikhonov_roots': _tikhonov_roots(item.lemma),
        'root_structures': structures_of(item.lemma),
        'provenance': assignment_of(item.lemma),
    }


def _side(surface, pack):
    readings = readings_of(surface)
    return {
        'surface': surface,
        'normalized': fold(surface),
        'readings': [_reading_dict(item) for item in readings],
        'guess_readings': [_reading_dict(item) for item in pack.reads],
        'lexemes': sorted(pack.lexemes),
        'root_structures': sorted(pack.roots),
        'families': sorted(pack.families),
        'assignment': assignment_of(surface),
        'reviewed_opaque': fold(surface) in REVIEWED_OPAQUE_ROOTS,
        'reviewed_derived': fold(surface) in REVIEWED_DERIVED_ROOTS,
        'tikhonov_roots': _tikhonov_roots(surface),
        'service_word': pack.service,
        'russian': pack.russian,
    }


def _layer_report(guess, target):
    gread = readings_of(guess)
    tread = readings_of(target)
    glex = {(x.para_id, x.lemma) for x in gread}
    tlex = {(x.para_id, x.lemma) for x in tread}
    groots = _roots(gread, guess)
    troots = _roots(tread, target)
    return {
        'EXACT': fold(guess) == fold(target),
        'SAME_LEXEME': bool(glex & tlex),
        'ROOT_SET_EQUALITY': bool(groots & troots),
        'IRREGULAR_GRAMMAR': _grammar(gread, tread),
        'ALIAS': _alias(fold(guess), fold(target)),
        'PROPER_PAIR': _proper_pair(fold(guess), fold(target)),
        'SERVICE_WORD': initially_open(guess) or initially_open(target),
        'russian_guess': bool(gread),
        'russian_target': bool(tread),
        'guess_roots': sorted(groots),
        'target_roots': sorted(troots),
        'shared_lexemes': sorted(glex & tlex),
    }


def _classification(report, guess, target, actual):
    if actual:
        return 'OPEN', 'no fix required'
    if report['SERVICE_WORD']:
        return 'SERVICE_WORD', 'service-word handling; no root assignment'
    if report['IRREGULAR_GRAMMAR']:
        return 'E', 'review IRREGULAR_GRAMMAR layer; current guess-side readings do not agree'
    if report['EXACT'] or report['SAME_LEXEME'] or report['ROOT_SET_EQUALITY'] or report['ALIAS']:
        return 'J', 'a non-runtime/all-readings signal exists; inspect resolver-side reading choice'
    gp = report['guess_roots']
    tp = report['target_roots']
    gtikh = _tikhonov_roots(guess)
    ttikh = _tikhonov_roots(target)
    if (not gp or not tp) and len(gtikh) == 1 and len(ttikh) == 1 and gtikh == ttikh:
        return 'B', 'review family as REVIEWED_OPAQUE_ROOT'
    if not gp or not tp:
        if fold(guess) in REVIEWED_DERIVED_ROOTS or fold(target) in REVIEWED_DERIVED_ROOTS:
            return 'C', 'review lemma as REVIEWED_DERIVED_ROOT'
        return 'G/H', 'inspect missing morphology or root segmentation'
    if set(gp) & set(tp) and gp != tp:
        return 'I', 'compound/full-structure mismatch is intentional'
    if families_of(guess) != families_of(target):
        return 'D', 'review semantic family split'
    return 'J', 'unknown; no automatic fix'


def explain_pair(guess, target):
    gp = _GuessPack(guess)
    tp = _Pack(target)
    layers = _layer_report(guess, target)
    actual = _relation(gp, tp)
    classification, recommendation = _classification(layers, guess, target, actual)
    return {
        'guess': _side(guess, gp),
        'target': _side(target, tp),
        'layers': layers,
        'runtime_reason': actual or 'CLOSED',
        'classification': classification,
        'recommendation': recommendation,
    }


def _fmt(value):
    if isinstance(value, dict):
        return ', '.join(f'{key}={_fmt(item)}' for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return '[' + ', '.join(_fmt(item) for item in value) + ']'
    return str(value)


def print_report(report):
    print(f"PAIR: {report['guess']['surface']} -> {report['target']['surface']}")
    for name in ('guess', 'target'):
        side = report[name]
        print(f"\n{name.upper()}: surface={side['surface']!r} normalized={side['normalized']!r}")
        print(f"  service_word={side['service_word']} russian={side['russian']}")
        print(f"  tikhonov_roots={_fmt(side['tikhonov_roots'])}")
        print(f"  root_structures={_fmt(side['root_structures'])}")
        print(f"  families={_fmt(side['families'])}")
        print(f"  assignment={_fmt(side['assignment'])}")
        print(f"  reviewed_opaque={side['reviewed_opaque']} reviewed_derived={side['reviewed_derived']}")
        print('  readings:')
        for reading in side['readings']:
            print('   - ' + _fmt(reading))
    print('\nLAYERS:')
    for key, value in report['layers'].items():
        if key not in {'guess_roots', 'target_roots', 'shared_lexemes'}:
            print(f'  {key}: {value}')
    print(f"  shared_lexemes: {_fmt(report['layers']['shared_lexemes'])}")
    print(f"  guess_roots: {_fmt(report['layers']['guess_roots'])}")
    print(f"  target_roots: {_fmt(report['layers']['target_roots'])}")
    print(f"\nFINAL: {report['runtime_reason'].upper()}")
    print(f"CLASSIFICATION: {report['classification']}")
    print(f"RECOMMENDATION: {report['recommendation']}")


def main():
    parser = argparse.ArgumentParser(description='Explain a Censorly lexical pair.')
    parser.add_argument('guess', nargs='?')
    parser.add_argument('target', nargs='?')
    parser.add_argument('--pairs-file', type=Path)
    args = parser.parse_args()
    if args.pairs_file:
        print('guess\ttarget\texpected\tactual\tprimary_reason\tclassification\tsuggested_fix_layer')
        for line in args.pairs_file.read_text(encoding='utf-8').splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            fields = line.split('\t')
            if len(fields) < 2:
                continue
            guess, target = fields[:2]
            expected = fields[2] if len(fields) > 2 else ''
            report = explain_pair(guess, target)
            actual = 'OPEN' if report['runtime_reason'] != 'CLOSED' else 'CLOSED'
            print('\t'.join((guess, target, expected, actual, report['runtime_reason'], report['classification'], report['recommendation'])))
        return 0
    if args.guess is None or args.target is None:
        parser.error('provide GUESS TARGET or --pairs-file FILE')
    print_report(explain_pair(args.guess, args.target))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

