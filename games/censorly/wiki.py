"""Fetch Russian Wikipedia plaintext articles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests

WIKI_API = 'https://ru.wikipedia.org/w/api.php'
USER_AGENT = 'InterovesCensorly/1.0 (https://interoves.com; game puzzle generator)'
MIN_BODY_CHARS = 400
# Keep payloads playable: full extracts create 20k+ DOM nodes / multi‑MB JSON.
MAX_BODY_CHARS = 12_000


class WikiFetchError(Exception):
    """Failed to load or validate a Wikipedia article."""


@dataclass(frozen=True)
class WikiArticle:
    title: str
    pageid: int
    extract: str


_TITLE_FROM_PATH = re.compile(r'^/wiki/([^?#]+)$')
_DISAMBIG_MARKERS = (
    'многозначный термин',
    'может означать',
    'может относиться',
    'список значений',
)


def title_from_user_input(raw: str) -> str:
    """Accept a bare title or ru.wikipedia.org URL."""
    text = (raw or '').strip()
    if not text:
        raise WikiFetchError('Пустой заголовок')
    if 'wikipedia.org' in text or text.startswith('http://') or text.startswith('https://'):
        parsed = urlparse(text)
        m = _TITLE_FROM_PATH.match(parsed.path or '')
        if m:
            return unquote(m.group(1).replace('_', ' '))
        qs = parse_qs(parsed.query or '')
        if qs.get('title'):
            return unquote(qs['title'][0].replace('_', ' '))
        raise WikiFetchError('Не удалось разобрать URL Википедии')
    return text


def _looks_like_disambiguation(extract: str) -> bool:
    head = (extract or '')[:400].lower().replace('ё', 'е')
    return any(marker in head for marker in _DISAMBIG_MARKERS)


def _trim_extract(extract: str) -> str:
    if len(extract) <= MAX_BODY_CHARS:
        return extract
    cut = extract[:MAX_BODY_CHARS]
    # Prefer ending on a paragraph boundary so we don't mid-word truncate.
    for sep in ('\n\n', '\n', '. '):
        idx = cut.rfind(sep)
        if idx >= MIN_BODY_CHARS:
            return cut[: idx + len(sep)].rstrip()
    return cut.rstrip()


def fetch_article(title: str, *, session: Optional[requests.Session] = None) -> WikiArticle:
    """Load plaintext extract for a Russian Wikipedia title."""
    title = title_from_user_input(title)
    sess = session or requests.Session()
    params = {
        'action': 'query',
        'format': 'json',
        'prop': 'extracts|info|pageprops',
        'ppprop': 'disambiguation',
        'explaintext': 1,
        'exsectionformat': 'plain',
        'redirects': 1,
        'titles': title,
        'inprop': 'displaytitle',
    }
    try:
        resp = sess.get(
            WIKI_API,
            params=params,
            headers={'User-Agent': USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise WikiFetchError(f'Не удалось загрузить статью: {exc}') from exc
    except ValueError as exc:
        raise WikiFetchError('Некорректный ответ Wikipedia API') from exc

    pages = (data.get('query') or {}).get('pages') or {}
    if not pages:
        raise WikiFetchError('Статья не найдена')
    page = next(iter(pages.values()))
    if page.get('missing') is not None or int(page.get('pageid') or 0) < 0:
        raise WikiFetchError('Статья не найдена')
    extract = (page.get('extract') or '').strip()
    resolved_title = (page.get('title') or title).strip()
    pageprops = page.get('pageprops') or {}
    if 'disambiguation' in pageprops:
        raise WikiFetchError('Это страница неоднозначности')
    if _looks_like_disambiguation(extract):
        raise WikiFetchError('Похоже на страницу неоднозначности')
    if len(extract) < MIN_BODY_CHARS:
        raise WikiFetchError('Статья слишком короткая')
    extract = _trim_extract(extract)
    return WikiArticle(
        title=resolved_title,
        pageid=int(page['pageid']),
        extract=extract,
    )
