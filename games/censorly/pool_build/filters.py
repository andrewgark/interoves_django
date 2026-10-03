"""Hard filters against the MediaWiki API (batched, cached)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests

from games.censorly.pool_build import (
    ARTICLE_POOL_DENY_PATH,
    BATCH_SIZE,
    MIN_LANGLINKS,
    MIN_WIKITEXT_BYTES,
    USER_AGENT,
    WIKI_API,
)
from games.censorly.tokenize import build_puzzle_payload, title_content_lemmas

WIKIDATA_API = 'https://www.wikidata.org/w/api.php'

_YEAR_TITLE_RE = re.compile(r'^\d{1,4}(\s*(год|годы|до\s*н\.?\s*э\.?))?$', re.I)
_LIST_TITLE_RE = re.compile(
    r'^(Список|Перечень|Индекс|Категория:|Портал:|Проект:)',
    re.I,
)
_EPHEMERA_RE = re.compile(
    r'(Список умерших|умерших в \d{4}|сезон \d{4}|чемпионат мира \d{4}|'
    r'евровидение[-\s]?\d{4}|выборы .*\d{4})',
    re.I,
)


def _session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({'User-Agent': USER_AGENT})
    return sess


def load_deny_titles(path: Path | None = None) -> set[str]:
    """Human-reviewed rejects (casefold) that must not re-enter the pool."""
    p = path or ARTICLE_POOL_DENY_PATH
    if not p.is_file():
        return set()
    out: set[str] = set()
    for line in p.read_text(encoding='utf-8').splitlines():
        t = line.strip()
        if t and not t.startswith('#'):
            out.add(t.casefold())
    return out


def title_heuristic_reject(title: str, *, deny: set[str] | None = None) -> str | None:
    t = (title or '').strip()
    if not t:
        return 'empty'
    if deny and t.casefold() in deny:
        return 'review_deny'
    if _LIST_TITLE_RE.match(t):
        return 'list_title'
    if _YEAR_TITLE_RE.match(t):
        return 'year_title'
    if _EPHEMERA_RE.search(t):
        return 'ephemera_title'
    if t.isdigit():
        return 'digits_only'
    return None


def title_has_guessable_lemma(title: str) -> bool:
    try:
        puzzle = build_puzzle_payload(wiki_title=title, body_text='заглушка текста.')
        return bool(title_content_lemmas(puzzle))
    except Exception:
        return False


def load_meta_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    # Drop stale few_langlinks rejects from the broken batch-langlinks probe.
    out = {}
    for key, val in raw.items():
        if isinstance(val, dict) and val.get('ok') is False and val.get('reject') == 'few_langlinks':
            continue
        out[key] = val
    return out


def save_meta_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding='utf-8')


def _query_info_batch(sess: requests.Session, titles: list[str]) -> dict[str, Any]:
    params = {
        'action': 'query',
        'format': 'json',
        'prop': 'info|pageprops',
        'ppprop': 'disambiguation',
        'inprop': 'length',
        'redirects': 1,
        'titles': '|'.join(titles),
    }
    for attempt in range(4):
        resp = sess.get(WIKI_API, params=params, timeout=90)
        if resp.status_code in (429, 503):
            time.sleep(1.5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json().get('query') or {}
    resp.raise_for_status()
    return {}


def _sitelink_counts(sess: requests.Session, titles: list[str]) -> dict[str, int]:
    """ruwiki title → number of Wikimedia sitelinks (incl. ruwiki)."""
    if not titles:
        return {}
    params = {
        'action': 'wbgetentities',
        'format': 'json',
        'sites': 'ruwiki',
        'titles': '|'.join(titles),
        'props': 'sitelinks',
    }
    for attempt in range(4):
        resp = sess.get(WIKIDATA_API, params=params, timeout=90)
        if resp.status_code in (429, 503):
            time.sleep(1.5 * (attempt + 1))
            continue
        resp.raise_for_status()
        data = resp.json()
        break
    else:
        return {}

    out: dict[str, int] = {}
    entities = data.get('entities') or {}
    # Map back via normalized / success entities.
    for _eid, ent in entities.items():
        if ent.get('missing') is not None:
            continue
        sitelinks = ent.get('sitelinks') or {}
        ru = sitelinks.get('ruwiki') or {}
        title = ru.get('title')
        if title:
            out[title.casefold()] = len(sitelinks)
    return out


def enrich_candidates(
    candidates: list[dict[str, Any]],
    *,
    cache_path: Path,
    min_bytes: int = MIN_WIKITEXT_BYTES,
    min_langlinks: int = MIN_LANGLINKS,
    progress_cb=None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply heuristics + API filters. Returns (accepted, rejected)."""
    cache = load_meta_cache(cache_path)
    sess = _session()
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    deny = load_deny_titles()

    pending: list[dict[str, Any]] = []
    for row in candidates:
        title = row['title']
        reason = title_heuristic_reject(title, deny=deny)
        if reason:
            rejected.append({**row, 'reject': reason})
            continue
        if not title_has_guessable_lemma(title):
            rejected.append({**row, 'reject': 'no_title_lemma'})
            continue
        cached = cache.get(title.casefold())
        if cached and cached.get('ok') is True:
            accepted.append({**row, **cached.get('meta', {}), 'canonical': cached.get('canonical', title)})
            continue
        if cached and cached.get('ok') is False:
            rejected.append({**row, 'reject': cached.get('reject', 'cached_reject')})
            continue
        pending.append(row)

    total_batches = (len(pending) + BATCH_SIZE - 1) // BATCH_SIZE or 1
    for batch_i in range(0, len(pending), BATCH_SIZE):
        batch = pending[batch_i: batch_i + BATCH_SIZE]
        if progress_cb:
            progress_cb(batch_i // BATCH_SIZE + 1, total_batches, len(batch))
        query = _query_info_batch(sess, [r['title'] for r in batch])
        redirects = {
            (r.get('from') or '').casefold(): r.get('to')
            for r in (query.get('redirects') or [])
        }
        normalized = {
            (r.get('from') or '').casefold(): r.get('to')
            for r in (query.get('normalized') or [])
        }
        pages_by_title = {}
        for page in (query.get('pages') or {}).values():
            pages_by_title[(page.get('title') or '').casefold()] = page

        # Resolve canonicals first, then ask Wikidata once per batch.
        resolved_rows: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
        for row in batch:
            orig = row['title']
            key = orig.casefold()
            resolved = redirects.get(key) or normalized.get(key) or orig
            page = pages_by_title.get(resolved.casefold())
            if page is None or page.get('missing') is not None:
                cache[key] = {'ok': False, 'reject': 'missing'}
                rejected.append({**row, 'reject': 'missing'})
                continue
            if 'disambiguation' in (page.get('pageprops') or {}):
                cache[key] = {
                    'ok': False,
                    'reject': 'disambiguation',
                    'canonical': page.get('title'),
                }
                rejected.append({**row, 'reject': 'disambiguation', 'canonical': page.get('title')})
                continue
            length = int(page.get('length') or 0)
            canonical = page.get('title') or resolved
            if length < min_bytes:
                cache[key] = {
                    'ok': False,
                    'reject': 'short_wikitext',
                    'canonical': canonical,
                    'meta': {'length': length, 'langlinks': 0},
                }
                rejected.append({
                    **row,
                    'reject': 'short_wikitext',
                    'canonical': canonical,
                    'length': length,
                })
                continue
            resolved_rows.append((row, canonical, page))

        sitelinks = _sitelink_counts(sess, [c for _r, c, _p in resolved_rows])
        for row, canonical, page in resolved_rows:
            key = row['title'].casefold()
            length = int(page.get('length') or 0)
            lang_n = int(sitelinks.get(canonical.casefold()) or 0)
            if lang_n < min_langlinks:
                cache[key] = {
                    'ok': False,
                    'reject': 'few_langlinks',
                    'canonical': canonical,
                    'meta': {'length': length, 'langlinks': lang_n},
                }
                rejected.append({
                    **row,
                    'reject': 'few_langlinks',
                    'canonical': canonical,
                    'length': length,
                    'langlinks': lang_n,
                })
                continue
            meta = {
                'length': length,
                'langlinks': lang_n,
                'pageid': page.get('pageid'),
            }
            cache[key] = {'ok': True, 'canonical': canonical, 'meta': meta}
            cache[canonical.casefold()] = {'ok': True, 'canonical': canonical, 'meta': meta}
            accepted.append({**row, **meta, 'canonical': canonical})

        if batch_i and batch_i % (BATCH_SIZE * 5) == 0:
            save_meta_cache(cache_path, cache)

    save_meta_cache(cache_path, cache)

    source_rank = {'vital1000': 0, 'vital10k': 1, 'current_pool': 2, 'pageviews': 3}
    by_canon: dict[str, dict[str, Any]] = {}
    for row in accepted:
        canon = (row.get('canonical') or row['title']).casefold()
        cur = by_canon.get(canon)
        if cur is None:
            by_canon[canon] = row
            continue
        cur_sources = cur.get('sources') or ([cur['source']] if cur.get('source') else [])
        new_sources = row.get('sources') or ([row['source']] if row.get('source') else [])
        cur_best = min(cur_sources or ['pageviews'], key=lambda s: source_rank.get(s, 99))
        new_best = min(new_sources or ['pageviews'], key=lambda s: source_rank.get(s, 99))
        merged = dict(row if source_rank.get(new_best, 99) < source_rank.get(cur_best, 99) else cur)
        themes = list(dict.fromkeys((cur.get('themes') or []) + (row.get('themes') or [])))
        sources = list(dict.fromkeys(cur_sources + new_sources))
        merged['themes'] = themes
        merged['sources'] = sources
        merged['pageviews'] = max(int(cur.get('pageviews') or 0), int(row.get('pageviews') or 0))
        by_canon[canon] = merged

    return list(by_canon.values()), rejected
