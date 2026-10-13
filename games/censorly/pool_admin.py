"""Read and update the tagged Censorly pool used by support and random play."""

import csv
from functools import lru_cache
from pathlib import Path

from games.models import CensorlyCategory, CensorlyObjectType, CensorlyPoolEntry

TAGS_PATH = Path(__file__).with_name('article_pool_tags.tsv')
DIFFICULTIES = ('легко', 'средне', 'сложно', 'очень сложно')


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
    overrides = list(CensorlyPoolEntry.objects.prefetch_related('categories', 'types'))
    category_parents = {
        category_id: (name, parent_id)
        for category_id, name, parent_id in CensorlyCategory.objects.values_list('pk', 'name', 'parent_id')
    } if overrides else {}
    for override in overrides:
        topics = [_category_path(category.pk, category_parents) for category in override.categories.all()]
        types = [item.name for item in override.types.all()]
        rows[override.title] = {
            'title': override.title, 'topics': topics or override.topics or [],
            'types': types or override.object_types or [], 'difficulty': override.difficulty,
            'enabled': override.enabled,
        }
    return rows


def _category_path(category_id, category_parents):
    names = []
    seen = set()
    current_id = category_id
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        node = category_parents.get(current_id)
        if node is None:
            break
        name, current_id = node
        names.append(name)
    return '/'.join(reversed(names))


def active_pool_titles(rows=None):
    """Pool titles with database additions and support-side exclusions applied."""
    from games.censorly.article_pool import load_article_pool

    rows = rows if rows is not None else pool_rows()
    titles = list(load_article_pool())
    known_titles = set(titles)
    titles.extend(title for title in rows if title not in known_titles)
    return [title for title in titles if rows.get(title, {}).get('enabled', True)]


def taxonomy(rows=None):
    categories = {}
    tree_nodes = []
    all_categories = list(
        CensorlyCategory.objects
        .select_related('parent')
        .order_by('parent_id', 'sort_order', 'name', 'pk')
    )
    all_tree_nodes = []
    if all_categories:
        children_by_parent = {}
        for item in all_categories:
            children_by_parent.setdefault(item.parent_id, []).append(item)
        ordered_all = []
        pending = [(item, None, frozenset(), True)
                   for item in reversed(children_by_parent.get(None, []))]
        while pending:
            item, parent_path, seen_ids, parents_active = pending.pop()
            if item.pk in seen_ids:
                continue
            path = (parent_path + '/' if parent_path else '') + item.name
            node = {'id': item.pk, 'name': item.name, 'parent_id': item.parent_id,
                    'path': path, 'sort_order': item.sort_order, 'is_active': item.is_active}
            all_tree_nodes.append(node)
            ordered_all.append((node, path, item.is_active and parents_active))
            next_seen = seen_ids | {item.pk}
            pending.extend((child, path, next_seen, item.is_active and parents_active)
                           for child in reversed(children_by_parent.get(item.pk, [])))
        for node, path, ancestors_active in ordered_all:
            if not ancestors_active:
                continue
            parts = path.split('/')
            categories.setdefault(parts[0], [])
            if len(parts) > 1:
                # Keep every descendant path relative to its root. The support
                # UI uses this map for both article tags and pool filtering.
                relative_path = '/'.join(parts[1:])
                if relative_path not in categories[parts[0]]:
                    categories[parts[0]].append(relative_path)
            tree_nodes.append({key: value for key, value in node.items() if key != 'is_active'})
    else:
        rows = rows if rows is not None else pool_rows()
        for row in rows.values():
            for path in row['topics']:
                parts = path.split('/', 1)
                categories.setdefault(parts[0], [])
                if len(parts) > 1:
                    relative_path = parts[1]
                    if relative_path not in categories[parts[0]]:
                        categories[parts[0]].append(relative_path)
        tree_nodes = []

    all_type_nodes = list(CensorlyObjectType.objects.values('id', 'name', 'sort_order', 'is_active'))
    if all_type_nodes:
        type_nodes = [item for item in all_type_nodes if item['is_active']]
        types = [item['name'] for item in type_nodes]
    else:
        rows = rows if rows is not None else pool_rows()
        types = sorted({name for row in rows.values() for name in row['types']})
        type_nodes = [{'id': name, 'name': name, 'sort_order': index} for index, name in enumerate(types)]
    return {
        'categories': categories, 'types': types, 'type_nodes': type_nodes,
        'tree': tree_nodes, 'all_tree': all_tree_nodes, 'all_type_nodes': all_type_nodes,
    }


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
    if enabled and not object_types:
        raise ValueError('Выберите хотя бы один тип объекта')
    known = taxonomy()
    valid_topics = {item['path']: item['id'] for item in known['tree']}
    if not valid_topics:
        valid_topics = set(known['categories']) | {
            parent + '/' + child for parent, children in known['categories'].items() for child in children
        }
    if any(topic not in valid_topics for topic in topics):
        raise ValueError('Выберите категории из таксономии')
    valid_types = {item.name: item.pk for item in CensorlyObjectType.objects.filter(is_active=True)}
    if not valid_types:
        valid_types = set(known['types'])
    if any(item not in valid_types for item in object_types):
        raise ValueError('Выберите типы из списка')
    entry, _ = CensorlyPoolEntry.objects.update_or_create(title=title, defaults={
        'topics': topics, 'object_types': object_types, 'difficulty': difficulty, 'enabled': enabled,
    })
    if isinstance(valid_topics, dict):
        entry.categories.set(valid_topics[value] for value in topics)
    if isinstance(valid_types, dict):
        entry.types.set(valid_types[value] for value in object_types)
