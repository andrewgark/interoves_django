"""Read and update the tagged Censorly pool used by support and random play."""

import csv
from pathlib import Path

from games.models import CensorlyPoolEntry

TAGS_PATH = Path(__file__).with_name('article_pool_tags.tsv')
DIFFICULTIES = ('легко', 'средне', 'сложно')


def pool_rows():
    rows = {}
    if TAGS_PATH.exists():
        with TAGS_PATH.open(encoding='utf-8', newline='') as stream:
            for item in csv.DictReader(stream, delimiter='\t'):
                title = (item.get('title') or '').strip()
                if title:
                    rows[title] = {
                        'title': title,
                        'topics': [v.strip() for v in (item.get('topics') or '').split(';') if v.strip()],
                        'types': [v.strip() for v in (item.get('object_types') or '').split(';') if v.strip()],
                        'difficulty': (item.get('difficulty') or '').strip(),
                        'enabled': True,
                    }
    for override in CensorlyPoolEntry.objects.all():
        rows[override.title] = {
            'title': override.title, 'topics': override.topics or [],
            'types': override.object_types or [], 'difficulty': override.difficulty,
            'enabled': override.enabled,
        }
    return rows


def taxonomy(rows=None):
    rows = rows or pool_rows()
    categories = {}
    types = set()
    for row in rows.values():
        types.update(row['types'])
        for path in row['topics']:
            if '/' in path:
                parent, child = path.split('/', 1)
                categories.setdefault(parent, set()).add(child)
            else:
                categories.setdefault(path, set())
    return {'categories': {key: sorted(value) for key, value in sorted(categories.items())}, 'types': sorted(types)}


def save_pool_entry(title, topics, object_types, difficulty, enabled=True):
    title = str(title or '').strip()
    if not title or len(title) > 255:
        raise ValueError('Укажите название статьи (до 255 символов)')
    if difficulty not in DIFFICULTIES:
        raise ValueError('Выберите сложность')
    CensorlyPoolEntry.objects.update_or_create(title=title, defaults={
        'topics': list(dict.fromkeys(topics)), 'object_types': list(dict.fromkeys(object_types)),
        'difficulty': difficulty, 'enabled': enabled,
    })
