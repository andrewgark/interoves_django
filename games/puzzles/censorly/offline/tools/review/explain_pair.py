"""Explain one Censorly lexical pair without changing resolver semantics."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
from django.apps import apps

# Also imported by the integrations worker; set up Django only for the CLI.
if not apps.ready:
    django.setup()

from games.puzzles.censorly.lexical import reviewed
from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.deciders.layers import (
    ALIAS,
    EXACT,
    GRAMMAR,
    LEXEME,
    PROPER,
    REVERSE_LEXEME,
    ROOT,
)
from games.puzzles.censorly.lexical.match import ANALYZER, MATCHER
from games.puzzles.censorly.lexical.match.rootbank import (
    assignment_of,
    families_of,
    root_components,
    sense_components,
    structures_of,
    tikhonov_roots_of,
)
from games.puzzles.censorly.lexical.semantics import readings_of
from games.puzzles.censorly.text.flags import open_pronouns_enabled


def _tikhonov_roots(lemma):
    return tikhonov_roots_of(lemma)


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
        'root_families': sorted({item for item in root_components(pack.roots)}),
        'sense_subclusters': sorted({item for item in sense_components(pack.roots)}),
        'families': sorted(pack.families),
        'assignment': assignment_of(surface),
        'reviewed_opaque': fold(surface) in reviewed.OPAQUE_ROOTS.data,
        'reviewed_derived': fold(surface) in reviewed.DERIVED_ROOTS.data,
        'tikhonov_roots': _tikhonov_roots(surface),
        'manual_root_assignment': assignment_of(surface)['source'] in {
            'SEMANTIC_ROOT_ASSIGNMENT', 'SEMANTIC_SPLIT_ROOTED',
            'REVIEWED_OPAQUE_ROOT', 'REVIEWED_DERIVED_ROOT',
            'REVIEWED_EXISTING_ROOT',
        },
        'service_word': pack.service,
        'russian': pack.russian,
    }


def _layer_report(guess, target, *, include_pronouns):
    # Each layer answers on its own, before the matcher's order and gates.
    gpack = ANALYZER.guess(guess, include_pronouns=include_pronouns)
    tpack = ANALYZER.target(target, include_pronouns=include_pronouns)
    glex = gpack.lexemes
    tlex = tpack.lexemes
    groots = gpack.roots
    troots = tpack.roots
    return {
        'EXACT': EXACT.opens(gpack, tpack),
        'SAME_LEXEME': LEXEME.opens(gpack, tpack) or REVERSE_LEXEME.opens(gpack, tpack),
        'ROOT_SET_EQUALITY': ROOT.opens(gpack, tpack),
        'IRREGULAR_GRAMMAR': GRAMMAR.opens(gpack, tpack),
        'ALIAS': ALIAS.opens(gpack, tpack),
        'PROPER_PAIR': PROPER.opens(gpack, tpack),
        'SERVICE_WORD': gpack.service or tpack.service,
        'russian_guess': gpack.russian,
        'russian_target': tpack.russian,
        'guess_roots': sorted(groots),
        'target_roots': sorted(troots),
        'guess_root_families': sorted({item for item in root_components(groots)}),
        'target_root_families': sorted({item for item in root_components(troots)}),
        'guess_senses': sorted({item for item in sense_components(groots)}),
        'target_senses': sorted({item for item in sense_components(troots)}),
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
        if fold(guess) in reviewed.DERIVED_ROOTS.data or fold(target) in reviewed.DERIVED_ROOTS.data:
            return 'C', 'review lemma as REVIEWED_DERIVED_ROOT'
        return 'G/H', 'inspect missing morphology or root segmentation'
    if set(gp) & set(tp) and gp != tp:
        return 'I', 'compound/full-structure mismatch is intentional'
    if families_of(guess) != families_of(target):
        return 'D', 'review semantic family split'
    return 'J', 'unknown; no automatic fix'


def explain_pair(guess, target):
    include_pronouns = open_pronouns_enabled()
    gp = ANALYZER.guess(guess, include_pronouns=include_pronouns)
    tp = ANALYZER.target(target, include_pronouns=include_pronouns)
    layers = _layer_report(guess, target, include_pronouns=include_pronouns)
    actual = MATCHER.relation(gp, tp)
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
        print(f"  root_families={_fmt(side['root_families'])}")
        print(f"  sense_subclusters={_fmt(side['sense_subclusters'])}")
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
    print(f"  guess_root_families: {_fmt(report['layers']['guess_root_families'])}")
    print(f"  target_root_families: {_fmt(report['layers']['target_root_families'])}")
    print(f"  guess_senses: {_fmt(report['layers']['guess_senses'])}")
    print(f"  target_senses: {_fmt(report['layers']['target_senses'])}")
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

