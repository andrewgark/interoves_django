"""Snapshot of everything the gameplay matcher decides, for refactoring.

Writes two files into ``--out``:

* ``bank.tsv``: every lemma of the root bank with its structures and source;
* ``decisions.tsv``: for each fixture article, each guess (every content
  surface of the article plus a fixed list of tricky guesses), the opened
  lemmas with their relation and the near-miss rejections.

A refactoring step that must not change behavior leaves both files
byte-identical. Compare with ``diff`` or the printed hashes.

    python -m games.puzzles.censorly.offline.tools.matching_snapshot --out /tmp/snap
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

import django

EXTRA_GUESSES = (
    '0', '1', '2', '5', '10', '12', '20', '100', '1000', 'X', 'XX', 'IV',
    'кг', 'км', 'килограмм', 'килограммов', 'фр', 'нем', 'немецкий', 'Германия',
    'г', 'год', 'лет', 'город', 'в', 'век', 'стр', 'страница', 'ул', 'улица',
    'н', 'наш', 'по', 'ПО', 'Ли', 'ли', 'теле', 'тело', 'кит', 'китайский',
    'Александр', 'Саша', 'Полина', 'Поля', 'поле', 'Москва', 'московский',
    'Россия', 'русский', 'рус', 'он', 'его', 'нём', 'это', 'эти', 'тот', 'том',
    'один', 'первый', 'два', 'второй', 'хуже', 'плохой', 'лучше', 'больше',
    'Троя', 'Трои', 'е', 'ш', 'кома', 'кому', 'ВОЗ', 'воз', 'есть', 'быть',
    'фамилия', 'поменять', 'юго', 'юг', 'северо-западный', 'англо',
    'неизвестный', 'известный', 'полчаса', 'час', 'обычно', 'обычный',
)


def _articles():
    from games.puzzles.censorly.offline.fixtures import iter_fixtures
    from games.puzzles.censorly.text.tokenize import build_puzzle_payload

    for name, _kind, body in iter_fixtures():
        payload = build_puzzle_payload(wiki_title='', body_text=body)
        yield name, payload['body_tokens']


def write_bank(path: Path) -> None:
    from games.puzzles.censorly.lexical.deciders import rootbank

    provenance = rootbank.ROOT_BANK.provenance()
    with path.open('w', encoding='utf-8') as out:
        for lemma in sorted(set(rootbank.ROOT_BANK.lemmas()) | set(provenance)):
            structures = rootbank.ROOT_BANK.structures_of(lemma)
            source = provenance.get(lemma, {}).get('source', 'ABSENT')
            rendered = ','.join('+'.join(item) for item in structures)
            out.write(f'{lemma}\t{source}\t{rendered}\n')


def write_decisions(path: Path) -> None:
    from games.puzzles.censorly.lexical.match import analyze_guess, matching_lemmas

    with path.open('w', encoding='utf-8') as out:
        for name, tokens in _articles():
            surfaces = sorted({
                tok.get('surface') or ''
                for tok in tokens
                if tok.get('kind') == 'content' and tok.get('surface')
            })
            for guess in (*surfaces, *EXTRA_GUESSES):
                hits, report = analyze_guess(tokens, guess, cache_key=name)
                plain = matching_lemmas(tokens, guess, cache_key=name)
                opened = sorted(f"{row['lemma']}:{row['reason']}" for row in report['opened'])
                rejected = sorted(f"{row['lemma']}:{row['reason']}" for row in report['rejected'])
                mark = '' if plain == hits else '\tPLAIN_DIFFERS=' + ','.join(sorted(plain))
                out.write(
                    f'{name}\t{guess}\t{",".join(sorted(hits))}\t'
                    f'{" ".join(opened)}\t{" ".join(rejected)}{mark}\n'
                )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--skip-decisions', action='store_true')
    args = parser.parse_args()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
    django.setup()
    args.out.mkdir(parents=True, exist_ok=True)
    bank = args.out / 'bank.tsv'
    write_bank(bank)
    print('bank', _sha(bank))
    if not args.skip_decisions:
        decisions = args.out / 'decisions.tsv'
        write_decisions(decisions)
        print('decisions', _sha(decisions))


if __name__ == '__main__':
    main()
