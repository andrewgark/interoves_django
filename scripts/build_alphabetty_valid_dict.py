#!/usr/bin/env python3
"""Собрать games/alphabetty/dictionaries/ru_words_valid.txt.gz

База: experiments/dictionaries/russian_words.txt (+ ru_nouns_valid).
Wiktionary: experiments/dictionaries/ru_wiktionary.txt
  (леммы ru.wiktionary, CC BY-SA 4.0;
   https://github.com/EgorTatarnikov/rus_dict_wiktionary — russian_dictionary.txt).
Имена собственные: списки имён/фамилий, города РФ, страны.

Пример::

    ../venv/interoves_django/bin/python scripts/build_alphabetty_valid_dict.py \\
        --names-dir /tmp/ru_proper_dicts
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DICT_DIR = _REPO / 'games' / 'alphabetty' / 'dictionaries'
_EXPERIMENTS = _REPO.parent / 'experiments' / 'dictionaries'
_CITIES_JSON = _REPO.parent / 'experiments' / 'russia_cities' / 'ru-cities.json'
_SUBJECTS_JSON = _REPO.parent / 'experiments' / 'russia_cities' / 'ru-subjects.json'

_WORD_PART = re.compile(r'[А-Я]+')


def normalize_token(raw: str) -> str:
    return (raw or '').strip().upper().replace('Ё', 'Е')


def add_cyrillic_tokens(bucket: set[str], text: str, *, min_len: int = 2) -> None:
    raw = normalize_token(text)
    if not raw:
        return
    for part in _WORD_PART.findall(raw):
        if len(part) >= min_len:
            bucket.add(part)


def load_word_file(path: Path, bucket: set[str]) -> int:
    if not path.is_file():
        return 0
    before = len(bucket)
    with path.open(encoding='utf-8', errors='ignore') as f:
        for line in f:
            # csv: take first column; plain txt: whole line
            cell = line.split(',')[0].split('\t')[0]
            add_cyrillic_tokens(bucket, cell)
    return len(bucket) - before


def load_json_countries(path: Path, bucket: set[str]) -> int:
    if not path.is_file():
        return 0
    before = len(bucket)
    data = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(data, dict):
        for name in data.values():
            add_cyrillic_tokens(bucket, str(name))
    return len(bucket) - before


def load_ru_cities(bucket: set[str]) -> int:
    before = len(bucket)
    if _CITIES_JSON.is_file():
        data = json.loads(_CITIES_JSON.read_text(encoding='utf-8'))
        for city in data.get('cities') or []:
            add_cyrillic_tokens(bucket, str(city.get('name') or ''))
            subj = city.get('subject')
            if isinstance(subj, dict):
                add_cyrillic_tokens(bucket, str(subj.get('name') or ''))
            elif isinstance(subj, str):
                add_cyrillic_tokens(bucket, subj)
    if _SUBJECTS_JSON.is_file():
        data = json.loads(_SUBJECTS_JSON.read_text(encoding='utf-8'))
        for subj in data.get('subjects') or []:
            add_cyrillic_tokens(bucket, str(subj.get('name') or ''))
            add_cyrillic_tokens(bucket, str(subj.get('center') or ''))
            # «Республика Адыгея» → АДЫГЕЯ уже из токенов; типные слова ок
    return len(bucket) - before


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        '--names-dir',
        type=Path,
        default=_EXPERIMENTS / 'proper',
        help='каталог с именами/фамилиями и countries_ru.json',
    )
    ap.add_argument(
        '--out',
        type=Path,
        default=_DICT_DIR / 'ru_words_valid.txt.gz',
    )
    args = ap.parse_args()
    names_dir: Path = args.names_dir

    words: set[str] = set()
    sources: list[tuple[str, int]] = []

    base_files = [
        _EXPERIMENTS / 'russian_words.txt',
        _DICT_DIR / 'ru_nouns_valid.txt',
        _EXPERIMENTS / 'ru_nouns_valid.txt',
        _EXPERIMENTS / 'countries_capitals_ru.txt',
        # Неологизмы / сленг / заимствования, которых нет в морфо-словаре.
        _EXPERIMENTS / 'ru_wiktionary.txt',
    ]
    for path in base_files:
        n = load_word_file(path, words)
        if n:
            sources.append((str(path), n))

    proper_files = [
        # Плоский каталог experiments/dictionaries/proper/ (предпочтительно)
        names_dir / 'russian_surnames.txt',
        names_dir / 'russian_names.txt',
        names_dir / 'russian_male_names.txt',
        names_dir / 'russian_female_names.txt',
        names_dir / 'male_names_rus.txt',
        names_dir / 'female_names_rus.txt',
        names_dir / 'male_surnames_rus.txt',
        # Либо сырые клоны репозиториев в --names-dir
        names_dir / 'russian_names-master' / 'russian_surnames.txt',
        names_dir / 'russian_names-master' / 'russian_names.txt',
        names_dir / 'russian_names-master' / 'russian_male_names.txt',
        names_dir / 'russian_names-master' / 'russian_female_names.txt',
        names_dir / 'ru-pnames-list-master' / 'lists' / 'male_names_rus.txt',
        names_dir / 'ru-pnames-list-master' / 'lists' / 'female_names_rus.txt',
        names_dir / 'ru-pnames-list-master' / 'lists' / 'male_surnames_rus.txt',
    ]
    for path in proper_files:
        n = load_word_file(path, words)
        if n:
            sources.append((str(path), n))

    n = load_json_countries(names_dir / 'countries_ru.json', words)
    if n:
        sources.append((str(names_dir / 'countries_ru.json'), n))

    n = load_ru_cities(words)
    if n:
        sources.append(('russia_cities json', n))

    if not words:
        print('no words collected', file=sys.stderr)
        return 1

    ordered = sorted(words)
    payload = ('\n'.join(ordered) + '\n').encode('utf-8')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(gzip.compress(payload, compresslevel=9))

    print(f'wrote {args.out} words={len(ordered)} bytes={args.out.stat().st_size}')
    for label, added in sources:
        print(f'  +{added:7d}  {label}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
