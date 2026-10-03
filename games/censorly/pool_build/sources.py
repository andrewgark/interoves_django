"""Harvest candidate titles from vital lists, pageviews, and the current pool."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any
from urllib.parse import unquote

import requests

from games.censorly.article_pool import load_article_pool
from games.censorly.pool_build import (
    PAGEVIEWS_TOP,
    USER_AGENT,
    VITAL_1000_PAGE,
    VITAL_10K_INDEX,
    WIKI_API,
)

_LINK_RE = re.compile(r'\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]')
_SERVICE_PREFIXES = (
    'Википедия:',
    'Служебная:',
    'Файл:',
    'Категория:',
    'Шаблон:',
    'Участник:',
    'Обсуждение:',
    'Portal:',
    'Портал:',
    'Category:',
    'File:',
    'Template:',
    'Special:',
    'Wikipedia:',
    'Help:',
    'Module:',
    'MediaWiki:',
)


def _session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({'User-Agent': USER_AGENT})
    return sess


def _is_service_title(title: str) -> bool:
    t = (title or '').strip()
    if not t:
        return True
    if t.startswith(':') or t.startswith('en:') or t.startswith('meta:'):
        return True
    if any(t.startswith(p) for p in _SERVICE_PREFIXES):
        return True
    if t.startswith('Заглавная') or t == 'Main Page':
        return True
    return False


def _normalize_title(raw: str) -> str | None:
    title = unquote((raw or '').strip().replace('_', ' '))
    if _is_service_title(title):
        return None
    # Drop interwiki / section leftovers.
    if ':' in title:
        prefix = title.split(':', 1)[0]
        if prefix.isascii() and prefix.islower():
            return None
        if prefix in ('en', 'meta', 'commons', 'wikt', 'd'):
            return None
    return title


def _parse_wikitext_titles(wikitext: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for match in _LINK_RE.finditer(wikitext or ''):
        title = _normalize_title(match.group(1))
        if not title:
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(title)
    return out


def _fetch_wikitext(sess: requests.Session, page: str) -> str:
    resp = sess.get(
        WIKI_API,
        params={
            'action': 'parse',
            'page': page,
            'prop': 'wikitext',
            'format': 'json',
        },
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    if 'error' in data:
        raise RuntimeError(f'parse {page}: {data["error"]}')
    return (data.get('parse') or {}).get('wikitext', {}).get('*') or ''


def _vital_10k_subpages(sess: requests.Session) -> list[tuple[str, str]]:
    """Return list of (theme_name, page_title)."""
    resp = sess.get(
        WIKI_API,
        params={
            'action': 'parse',
            'page': VITAL_10K_INDEX,
            'prop': 'links',
            'format': 'json',
        },
        timeout=90,
    )
    resp.raise_for_status()
    links = (resp.json().get('parse') or {}).get('links') or []
    prefix = 'Википедия:Список статей, которые должны быть во всех языковых версиях/10000/'
    out: list[tuple[str, str]] = []
    for link in links:
        name = link.get('*') or ''
        if not name.startswith(prefix):
            continue
        theme = name[len(prefix):].strip()
        if theme:
            out.append((theme, name))
    return out


def harvest_vital_1000(sess: requests.Session) -> list[dict[str, Any]]:
    wt = _fetch_wikitext(sess, VITAL_1000_PAGE)
    return [
        {'title': t, 'source': 'vital1000', 'theme': 'vital1000'}
        for t in _parse_wikitext_titles(wt)
    ]


def harvest_vital_10k(sess: requests.Session) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for theme, page in _vital_10k_subpages(sess):
        wt = _fetch_wikitext(sess, page)
        for title in _parse_wikitext_titles(wt):
            rows.append({'title': title, 'source': 'vital10k', 'theme': theme})
    return rows


def _pageviews_day_urls(days: int = 30) -> list[str]:
    # Pageviews top often lags a couple of days; walk recent calendar days.
    today = date.today()
    urls = []
    for delta in range(2, 2 + days):
        d = today - timedelta(days=delta)
        urls.append(f'{PAGEVIEWS_TOP}/{d.year:04d}/{d.month:02d}/{d.day:02d}')
    return urls


def harvest_pageviews(sess: requests.Session, *, days: int = 30) -> list[dict[str, Any]]:
    views: dict[str, int] = defaultdict(int)
    used_days = 0
    for url in _pageviews_day_urls(days=days):
        resp = sess.get(url, timeout=60)
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        items = (resp.json().get('items') or [{}])[0].get('articles') or []
        used_days += 1
        for row in items:
            article = _normalize_title((row.get('article') or '').replace('_', ' '))
            if not article:
                continue
            views[article] += int(row.get('views') or 0)
    ranked = sorted(views.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        {
            'title': title,
            'source': 'pageviews',
            'theme': 'pageviews',
            'pageviews': count,
            'pageviews_days': used_days,
        }
        for title, count in ranked
    ]


def harvest_current_pool() -> list[dict[str, Any]]:
    return [
        {'title': t, 'source': 'current_pool', 'theme': 'current_pool'}
        for t in load_article_pool()
    ]


def merge_harvest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Dedupe by casefold title; keep best source priority + themes."""
    source_rank = {
        'vital1000': 0,
        'vital10k': 1,
        'current_pool': 2,
        'pageviews': 3,
    }
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        title = row['title']
        key = title.casefold()
        cur = by_key.get(key)
        if cur is None:
            by_key[key] = {
                'title': title,
                'sources': [row['source']],
                'themes': [row['theme']] if row.get('theme') else [],
                'pageviews': int(row.get('pageviews') or 0),
            }
            continue
        if row['source'] not in cur['sources']:
            cur['sources'].append(row['source'])
        theme = row.get('theme')
        if theme and theme not in cur['themes']:
            cur['themes'].append(theme)
        cur['pageviews'] = max(cur['pageviews'], int(row.get('pageviews') or 0))
        # Prefer higher-priority source's title casing.
        best = min(cur['sources'], key=lambda s: source_rank.get(s, 99))
        if row['source'] == best:
            cur['title'] = title

    candidates = sorted(by_key.values(), key=lambda r: r['title'].casefold())
    return {
        'generated_at': date.today().isoformat(),
        'candidate_count': len(candidates),
        'candidates': candidates,
    }


def run_harvest(*, pageview_days: int = 30) -> dict[str, Any]:
    sess = _session()
    rows: list[dict[str, Any]] = []
    rows.extend(harvest_vital_1000(sess))
    rows.extend(harvest_vital_10k(sess))
    rows.extend(harvest_pageviews(sess, days=pageview_days))
    rows.extend(harvest_current_pool())
    return merge_harvest(rows)
