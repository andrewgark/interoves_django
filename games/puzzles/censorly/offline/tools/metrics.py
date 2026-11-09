"""Censorly matching metrics: coverage by word group, and correctness.

See ``offline/metrics.py`` for what each number means. Without a
subcommand it prints the whole set; paste the ``METRICS`` lines into the
roadmap journal::

    ../venv/interoves_django/bin/python -m games.puzzles.censorly.offline.tools.metrics
    … metrics coverage            # both corpora, by word group
    … metrics gold [--show 20]    # bee.roots.json precision / recall
    … metrics correctness         # every labeled sample, by link group
    … metrics draw --per-group 30 --extra 100 --seed 7   # a new sample to label

A new sample goes to ``lexical/data/metrics/correctness_<date>_s<seed>.tsv``
with an empty ``label`` column: fill it with OK or BAD (anything else is
skipped). OK means a player would accept that the guess opens the word: the
same school root, as in ``bee.roots.json``. Fixing exactly the labeled BAD
pairs inflates that sample's later precision, so the honest current number
always comes from a fresh sample.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
from django.apps import apps

if not apps.ready:
    django.setup()

from games.puzzles.censorly.offline import metrics as m
from games.puzzles.censorly.offline.corpus import load_corpus


def _pct(value: float) -> str:
    return f'{100 * value:.1f}%'


def coverage_lines() -> list[str]:
    lines = []
    for name, holdout in (('main', False), ('holdout', True)):
        cov = m.coverage(load_corpus(holdout=holdout))
        parts = [f'all={_pct(cov.share())}'] + [
            f'{group}={_pct(cov.share(group))}' for group in m.WORD_GROUPS
        ]
        mix = ' '.join(
            f'{group}:{_pct(cov.tokens[group] / max(sum(cov.tokens.values()), 1))}'
            for group in m.WORD_GROUPS
        )
        lines.append(f'METRICS coverage[{name}] ' + ' '.join(parts) + f'  (token mix {mix})')
    return lines


def gold_lines(show: int = 0) -> list[str]:
    result = m.gold_article()
    lines = [
        f'METRICS gold[bee] precision={_pct(result.precision)} recall={_pct(result.recall)} '
        f'lemmas={result.lemmas} gold_pairs={result.gold_pairs} opened={result.opened_pairs}'
    ]
    if show:
        lines.append('  false opens: ' + ', '.join(f'{a}→{b}' for a, b in result.false_opens[:show]))
        lines.append('  missed:      ' + ', '.join(f'{a}→{b}' for a, b in result.missed[:show]))
    return lines


def correctness_lines(verbose: bool = True) -> list[str]:
    lines = []
    for sample in m.load_samples():
        scores = m.score(sample)
        weights = sample.meta.get('group_weights', {})
        labeled = sum(item.labeled for item in scores.values())
        if not labeled:
            lines.append(f'METRICS correctness[{sample.path.stem}] unlabeled')
            continue
        then = m.weighted_precision(scores, weights, now=False)
        now = m.weighted_precision(scores, weights, now=True)
        lines.append(
            f'METRICS correctness[{sample.path.stem}] precision={_pct(then)} at labeling, '
            f'{_pct(now)} now (contaminated if fixes used this sample) labeled={labeled}'
        )
        if not verbose:
            continue
        for group in m.LINK_GROUPS:
            item = scores.get(group)
            if not item:
                continue
            low, high = m.wilson(item.ok, item.labeled)
            lines.append(
                f'  {group:16s} share={_pct(weights.get(group, 0)):>6s} '
                f'precision={_pct(item.ok / item.labeled):>6s} [{_pct(low)}–{_pct(high)}] '
                f'n={item.labeled:3d} bad_fixed={item.bad_fixed} ok_lost={item.ok_lost}'
            )
    return lines or ['METRICS correctness: no labeled samples yet (run `metrics draw`)']


def draw(per_group: int, seed: int, pool: int, extra: int) -> None:
    corpus = load_corpus(holdout=True)
    sample, weights = m.stratified_sample(corpus, per_group, seed, pool, extra)
    m.DATA_DIR.mkdir(parents=True, exist_ok=True)
    stem = f'correctness_{dt.date.today().isoformat()}_s{seed}'
    path = m.DATA_DIR / f'{stem}.tsv'
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=m.LABEL_FIELDS, delimiter='\t', lineterminator='\n')
        writer.writeheader()
        for row in sample:
            writer.writerow({**row, 'label': '', 'note': ''})
    meta = {
        'drawn_at': dt.date.today().isoformat(),
        'seed': seed,
        'corpus': corpus.source,
        'pool_size': pool,
        'per_group': per_group,
        'extra_proportional': extra,
        'group_weights': weights,
    }
    path.with_suffix('.meta.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8',
    )
    print(f'{len(sample)} pairs to label -> {path}')
    for group, weight in sorted(weights.items(), key=lambda item: -item[1]):
        print(f'  {group:16s} {_pct(weight)}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('coverage')
    gold = sub.add_parser('gold')
    gold.add_argument('--show', type=int, default=20)
    sub.add_parser('correctness')
    drawing = sub.add_parser('draw')
    drawing.add_argument('--per-group', type=int, default=40)
    drawing.add_argument('--seed', type=int, default=1)
    drawing.add_argument('--pool', type=int, default=4000, help='openings drawn to weigh the groups')
    drawing.add_argument(
        '--extra', type=int, default=0,
        help='more pairs spread by group share (narrows the overall interval)',
    )
    args = parser.parse_args()

    if args.command == 'coverage':
        print('\n'.join(coverage_lines()))
    elif args.command == 'gold':
        print('\n'.join(gold_lines(args.show)))
    elif args.command == 'correctness':
        print('\n'.join(correctness_lines()))
    elif args.command == 'draw':
        draw(args.per_group, args.seed, args.pool, args.extra)
    else:
        print('\n'.join(coverage_lines() + gold_lines() + correctness_lines(verbose=False)))


if __name__ == '__main__':
    main()
