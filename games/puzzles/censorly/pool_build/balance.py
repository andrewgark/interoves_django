"""Theme quota fill for the draft pool."""

from __future__ import annotations

import random
from collections import Counter
from typing import Any

from games.censorly.pool_build import THEME_BUCKET_CAPS, THEME_TO_BUCKET


def primary_theme(row: dict[str, Any]) -> str | None:
    for theme in row.get('themes') or []:
        if theme in THEME_TO_BUCKET:
            return theme
    return None


def bucket_for(row: dict[str, Any]) -> str:
    theme = primary_theme(row)
    if theme:
        return THEME_TO_BUCKET[theme]
    sources = row.get('sources') or []
    if 'vital1000' in sources:
        return 'society'
    return 'fill'


def _source_priority(row: dict[str, Any]) -> tuple:
    sources = set(row.get('sources') or [])
    return (
        0 if 'vital1000' in sources else 1,
        0 if 'vital10k' in sources else 1,
        0 if 'current_pool' in sources else 1,
        -int(row.get('pageviews') or 0),
        -int(row.get('langlinks') or 0),
        -int(row.get('length') or 0),
        (row.get('canonical') or row.get('title') or '').casefold(),
    )


def balance_pool(
    accepted: list[dict[str, Any]],
    *,
    target_size: int = 10000,
    caps: dict[str, float] | None = None,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    caps = caps or THEME_BUCKET_CAPS
    rng = random.Random(seed)

    must_keep = [r for r in accepted if 'current_pool' in (r.get('sources') or [])]
    by_bucket: dict[str, list[dict[str, Any]]] = {k: [] for k in caps}
    for row in accepted:
        by_bucket.setdefault(bucket_for(row), []).append(row)

    for bucket, rows in by_bucket.items():
        rows.sort(key=_source_priority)
        if bucket == 'fill':
            rng.shuffle(rows)
            rows.sort(key=lambda r: -int(r.get('pageviews') or 0))

    selected: dict[str, dict[str, Any]] = {}

    def add(row: dict[str, Any]) -> bool:
        key = (row.get('canonical') or row['title']).casefold()
        if key in selected:
            return False
        selected[key] = row
        return True

    def bucket_count(name: str) -> int:
        return sum(1 for r in selected.values() if bucket_for(r) == name)

    for row in must_keep:
        add(row)

    # Prefer all vital1000 that passed hard filters.
    for row in accepted:
        if 'vital1000' in (row.get('sources') or []):
            add(row)

    # Fill each bucket up to its cap.
    for bucket, share in caps.items():
        limit = max(0, int(target_size * share))
        for row in by_bucket.get(bucket, []):
            if len(selected) >= target_size:
                break
            if bucket_count(bucket) >= limit:
                break
            add(row)
        if len(selected) >= target_size:
            break

    # Top up from remaining by global priority.
    if len(selected) < target_size:
        for row in sorted(accepted, key=_source_priority):
            if len(selected) >= target_size:
                break
            add(row)

    final = sorted(
        selected.values(),
        key=lambda r: (r.get('canonical') or r['title']).casefold(),
    )
    report = {
        'selected': len(final),
        'target_size': target_size,
        'bucket_counts': dict(Counter(bucket_for(r) for r in final)),
        'theme_counts': dict(Counter(primary_theme(r) or 'untagged' for r in final)),
        'must_keep_current_pool': len(must_keep),
    }
    return final, report
