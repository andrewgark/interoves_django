"""Fetch Russian Wikipedia plaintext articles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlparse

import requests

WIKI_API = 'https://ru.wikipedia.org/w/api.php'
USER_AGENT = 'InterovesCensorly/1.0 (https://interoves.com; game puzzle generator)'
MIN_BODY_CHARS = 400
MAX_BODY_CHARS = 80_000


class WikiFetchError(Exception):
    """Failed to load or validate a Wikipedia article."""


@dataclass(frozen=True)
class WikiArticle:
    title: str
    pageid: int
    extract: str


_TITLE_FROM_PATH = re.compile(r'^/wiki/([^?#]+)$')


def title_from_user_input(raw: str) -> str:
    """Accept a bare title or ru.wikipedia.org URL."""
    text = (raw or '').strip()
    if not text:
        raise WikiFetchError('Пустой заголовок')
    if 'wikipedia.org' in text or text.startswith('http://') or text.startswith('https://'):
        parsed = urlparse(text)
        m = _TITLE_FROM_PATH.match(parsed.path or '')
        if not m:
            raise WikiFetchError('Не удалось разобрать URL Википедии')
        return unquote(m.group(1).replace('_', ' '))
    return text


def fetch_article(title: str, *, session: Optional[requests.Session] = None) -> WikiArticle:
    """Load plaintext extract for a Russian Wikipedia title."""
    title = title_from_user_input(title)
    sess = session or requests.Session()
    params = {
        'action': 'query',
        'format': 'json',
        'prop': 'extracts|info',
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
    if page.get('pageprops', {}).get('disambiguation') is not None:
        raise WikiFetchError('Это страница неоднозначности')
    # Heuristic: disambiguation pages often start with «… — многозначный термин»
    if 'может означать' in extract[:200].lower() and len(extract) < 2000:
        raise WikiFetchError('Похоже на страницу неоднозначности')
    if len(extract) < MIN_BODY_CHARS:
        raise WikiFetchError('Статья слишком короткая')
    if len(extract) > MAX_BODY_CHARS:
        extract = extract[:MAX_BODY_CHARS].rsplit('\n', 1)[0] or extract[:MAX_BODY_CHARS]
    return WikiArticle(
        title=resolved_title,
        pageid=int(page['pageid']),
        extract=extract,
    )
