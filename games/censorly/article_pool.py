"""Curated Russian Wikipedia titles for random Цензурки."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_POOL_PATH = Path(__file__).with_name('article_pool.txt')


@lru_cache(maxsize=1)
def load_article_pool() -> tuple[str, ...]:
    if not _POOL_PATH.is_file():
        return ()
    titles: list[str] = []
    seen: set[str] = set()
    for line in _POOL_PATH.read_text(encoding='utf-8').splitlines():
        title = line.strip()
        if not title or title.startswith('#'):
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        titles.append(title)
    return tuple(titles)
