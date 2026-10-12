"""Read and update the tagged Censorly pool used by support and random play."""

import csv
from functools import lru_cache
from pathlib import Path

from games.models import CensorlyPoolEntry

TAGS_PATH = Path(__file__).with_name('article_pool_tags.tsv')
DIFFICULTIES = ('легко', 'средне', 'сложно')


@lru_cache(maxsize=4)
def _base_pool_rows(mtime_ns, file_size):
    rows = {}
    if TAGS_PATH.exists():
        with TAGS_PATH.open(encoding='utf-8-sig', newline='') as stream:
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
    return rows


def _cached_base_pool_rows():
    try:
        stat = TAGS_PATH.stat()
    except FileNotFoundError:
        return {}
    return _base_pool_rows(stat.st_mtime_ns, stat.st_size)


def pool_rows():
    rows = {title: {**row, 'topics': list(row['topics']), 'types': list(row['types'])}
            for title, row in _cached_base_pool_rows().items()}
    for override in CensorlyPoolEntry.objects.all():
        rows[override.title] = {
            'title': override.title, 'topics': override.topics or [],
            'types': override.object_types or [], 'difficulty': override.difficulty,
            'enabled': override.enabled,
        }
    return rows


def active_pool_titles(rows=None):
    """Pool titles with database additions and support-side exclusions applied."""
    from games.censorly.article_pool import load_article_pool

    rows = rows if rows is not None else pool_rows()
    titles = list(load_article_pool())
    known_titles = set(titles)
    titles.extend(title for title in rows if title not in known_titles)
    return [title for title in titles if rows.get(title, {}).get('enabled', True)]


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
    if not isinstance(enabled, bool):
        raise ValueError('Некорректный статус статьи')
    if not isinstance(topics, (list, tuple)) or not isinstance(object_types, (list, tuple)):
        raise ValueError('Проверьте выбранные теги')
    if any(not isinstance(value, str) for value in (*topics, *object_types)):
        raise ValueError('Проверьте выбранные теги')
    topics = list(dict.fromkeys(v.strip() for v in topics if isinstance(v, str) and v.strip()))
    object_types = list(dict.fromkeys(v.strip() for v in object_types if isinstance(v, str) and v.strip()))
    if enabled and not topics:
        raise ValueError('Выберите хотя бы одну категорию')
    known = taxonomy()
    valid_topics = set(known['categories']) | {
        parent + '/' + child for parent, children in known['categories'].items() for child in children
    }
    if any(topic not in valid_topics for topic in topics):
        raise ValueError('Выберите категории из таксономии')
    if any(item not in known['types'] for item in object_types):
        raise ValueError('Выберите типы из списка')
    CensorlyPoolEntry.objects.update_or_create(title=title, defaults={
        'topics': topics, 'object_types': object_types,
        'difficulty': difficulty, 'enabled': enabled,
    })
