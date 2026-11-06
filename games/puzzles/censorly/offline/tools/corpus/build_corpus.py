"""Build the Censorly prioritisation corpus from the random-game article pool.

A deterministic, topic-stratified sample of ``article/article_pool.txt`` is
fetched with the gameplay ``fetch_article`` and tokenized with the puzzle
payload builder, so lemmas are exactly what a player meets. Article texts
are cached under ``var/censorly_corpus/`` (git-ignored, resumable); only
lemma frequencies and the sampled titles are committed::

    ../venv/interoves_django/bin/python -m \
        games.puzzles.censorly.offline.tools.corpus.build_corpus --size 1000
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
from django.apps import apps

if not apps.ready:
    django.setup()

import requests

from games.puzzles.censorly.article.pool import load_article_pool
from games.puzzles.censorly.article.wiki import (
    CensorlyArticleRejected,
    WikipediaRequestFailed,
    fetch_article,
)
from games.puzzles.censorly.lexical.match.core import fold
from games.puzzles.censorly.lexical.semantics import initially_open
from games.puzzles.censorly.offline.corpus import DATA_DIR, FREQ_PATH, META_PATH
from games.puzzles.censorly.text.tokenize import build_puzzle_payload

ROOT = Path(__file__).resolve().parents[6]
CACHE_DIR = ROOT / 'var' / 'censorly_corpus' / 'articles'
TAGS = Path(__file__).resolve().parents[3] / 'article' / 'article_pool_tags.tsv'
TITLES_PATH = DATA_DIR / 'corpus_titles.tsv'


def _key(title: str) -> str:
    return hashlib.sha1(title.encode('utf-8')).hexdigest()


def sample_titles(size: int) -> list[tuple[str, str]]:
    """Topic-proportional sample; inside a topic, the lowest title hashes."""
    topics = {}
    if TAGS.exists():
        with TAGS.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream, delimiter='\t'):
                topic = (row.get('topics') or '').split('/')[0].split(';')[0].strip()
                topics[row['title'].strip()] = topic or 'Прочее'
    by_topic: dict[str, list[str]] = defaultdict(list)
    for title in load_article_pool():
        by_topic[topics.get(title, 'Прочее')].append(title)
    total = sum(map(len, by_topic.values()))
    picked = []
    for topic, titles in sorted(by_topic.items()):
        quota = max(1, round(size * len(titles) / total))
        picked.extend((title, topic) for title in sorted(titles, key=_key)[:quota])
    return sorted(picked, key=lambda item: _key(item[0]))[:size]


def cached_article(title: str, session: requests.Session, delay: float) -> dict | None:
    path = CACHE_DIR / f'{_key(title)}.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    for attempt in range(3):
        try:
            article = fetch_article(title, session=session)
            break
        except CensorlyArticleRejected:
            data = {'title': title, 'rejected': True}
            path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            return data
        except WikipediaRequestFailed:
            time.sleep(5 * (attempt + 1))
    else:
        return None
    data = {
        'title': title,
        'resolved_title': article.title,
        'revid': article.revid,
        'body': article.extract,
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    time.sleep(delay)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--size', type=int, default=1000)
    parser.add_argument('--delay', type=float, default=0.5, help='seconds between API calls')
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    titles = sample_titles(args.size)
    tokens, documents, surfaces = Counter(), Counter(), defaultdict(Counter)
    service = 0
    used = []
    started = time.perf_counter()
    for index, (title, topic) in enumerate(titles, 1):
        data = cached_article(title, session, args.delay)
        if not data or data.get('rejected'):
            continue
        payload = build_puzzle_payload(wiki_title=data['resolved_title'], body_text=data['body'])
        seen = set()
        for token in payload['title_tokens'] + payload['body_tokens']:
            surface = token.get('surface') or ''
            if token.get('kind') != 'content' or not surface:
                continue
            if initially_open(surface):
                service += 1
                continue
            lemma = fold(token.get('lemma') or surface)
            tokens[lemma] += 1
            surfaces[lemma][fold(surface)] += 1
            seen.add(lemma)
        documents.update(seen)
        used.append((title, topic, data.get('revid') or ''))
        if index % 50 == 0:
            print(f'{index}/{len(titles)} articles, {len(tokens)} lemmas, '
                  f'{time.perf_counter() - started:.0f}s', flush=True)

    # mtime=0 keeps the archive byte-identical when the counts are unchanged.
    with open(FREQ_PATH, 'wb') as raw, gzip.GzipFile(
        fileobj=raw, mode='wb', compresslevel=9, mtime=0,
    ) as packed, io.TextIOWrapper(packed, encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
        writer.writerow(['lemma', 'tokens', 'articles', 'top_surface'])
        for lemma, count in sorted(tokens.items(), key=lambda item: (-item[1], item[0])):
            writer.writerow([lemma, count, documents[lemma], surfaces[lemma].most_common(1)[0][0]])
    with TITLES_PATH.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
        writer.writerow(['title', 'topic', 'revid'])
        writer.writerows(sorted(used))
    meta = {
        'articles': len(used),
        'requested': len(titles),
        'content_tokens': sum(tokens.values()),
        'service_tokens': service,
        'lemmas': len(tokens),
        'topics': dict(Counter(topic for _title, topic, _revid in used).most_common()),
        'built_with': 'games/puzzles/censorly/offline/tools/corpus/build_corpus.py',
    }
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
