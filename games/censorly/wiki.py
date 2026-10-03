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
# Cap for playable payloads; full Москва extract is ~100k chars / huge DOM.
MAX_BODY_CHARS = 28_000

# Trailing wiki sections that add noise for guessing (notes, links, nav).
_TAIL_SECTION_NAMES = (
    'примечания',
    'литература',
    'ссылки',
    'внешние ссылки',
    'см. также',
    'см также',
    'источники',
    'примечания и ссылки',
    'литература и ссылки',
    'галерея',
    'навигация',
    'категории',
)

_SECTION_HEADING_RE = re.compile(
    r'^(={2,})\s*(.+?)\s*\1\s*$',
    re.MULTILINE,
)

class WikiFetchError(Exception):
    """Failed to load or validate a Wikipedia article."""


# Citation footnotes [1] / [12] — not crystallographic Miller indices [100]/[010].
_NUMERIC_FOOTNOTE_RE = re.compile(r'\[(\d{1,2})\]')
# Common ruwiki editorial / citation-needed bracket notes.
_EDITORIAL_BRACKET_RE = re.compile(
    r'\[(?:'
    r'уточнить|кто\?|когда\?|где\?|какой\?|какая\?|какое\?|какие\?|сколько\?|'
    r'источник\??|нужен\s+источник|нет\s+источника'
    r')\]',
    re.IGNORECASE,
)
# Lead pronunciation dumps: «Москва (МФА: […])».
_MFA_PRONUNCIATION_RE = re.compile(
    r'(?:,?\s*)?\(?\s*МФА\s*:\s*\[[^\]]*\]\s*\)?',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WikiArticle:
    title: str
    pageid: int
    extract: str
    truncated: bool = False


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


def _normalize_section_name(name: str) -> str:
    return (name or '').strip().lower().replace('ё', 'е').rstrip('.')


def _strip_tail_sections(extract: str) -> str:
    """Drop Примечания / Ссылки / … and everything after the first such heading."""
    if not extract:
        return extract
    earliest = None
    for match in _SECTION_HEADING_RE.finditer(extract):
        name = _normalize_section_name(match.group(2))
        # Exact name, or "Ссылки …" / "Примечания и …" — not "Литературный обзор".
        is_tail = name in _TAIL_SECTION_NAMES or any(
            name == base or name.startswith(base + ' ') or name.startswith(base + ' и ')
            for base in _TAIL_SECTION_NAMES
        )
        if is_tail:
            earliest = match.start() if earliest is None else min(earliest, match.start())
    if earliest is None or earliest < MIN_BODY_CHARS:
        return extract
    return extract[:earliest].rstrip()


def _strip_orphan_heading(extract: str) -> str:
    """Remove a trailing == Section == with no body after it."""
    text = (extract or '').rstrip()
    match = list(_SECTION_HEADING_RE.finditer(text))
    if not match:
        return text
    last = match[-1]
    after = text[last.end():].strip()
    if after:
        return text
    # Keep if the whole article is somehow just a heading (shouldn't happen).
    if last.start() < MIN_BODY_CHARS // 2:
        return text
    return text[: last.start()].rstrip()


def _remove_balanced_tex_groups(text: str) -> str:
    """Drop `{ \\command … }` groups (typically `{\\displaystyle …}`) with brace balance."""
    if not text or '\\' not in text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '{' and i + 1 < n and text[i + 1] == '\\':
            depth = 0
            j = i
            while j < n:
                ch = text[j]
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            i = j
            continue
        out.append(text[i])
        i += 1
    return ''.join(out)


def _strip_indented_math_lines(text: str) -> str:
    """Drop indented MathML-plaintext glyph lines left beside TeX groups."""
    if not text:
        return text
    kept: list[str] = []
    for line in text.splitlines():
        # Prose and == headings start at column 0; math dumps are indented.
        if line.startswith('  ') or line.startswith('\t'):
            continue
        kept.append(line)
    return '\n'.join(kept)


def _collapse_extract_whitespace(text: str) -> str:
    """Normalize blanks after math/image cleanup so prose reads as paragraphs."""
    if not text:
        return text
    text = text.replace('\u2061', '')  # function-application (MathML)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Punctuation that followed a removed display formula.
    text = re.sub(r'\n{2,}[ \t]*([,.;:!?…])', r'\1', text)
    # Mid-sentence leftovers: "скорость\n\nсоответствует" / "скорость\n соответствует".
    text = re.sub(r'([^\n])\n{2,}[ \t]*([а-яёa-z])', r'\1 \2', text)
    text = re.sub(r'([^\n])\n[ \t]*([а-яёa-z])', r'\1 \2', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


def _strip_bracket_notes(text: str) -> str:
    """Drop citation footnotes and editorial [уточнить]-style notes; keep [100]/Miller."""
    if not text or '[' not in text:
        return text
    text = _MFA_PRONUNCIATION_RE.sub('', text)
    text = _NUMERIC_FOOTNOTE_RE.sub('', text)
    text = _EDITORIAL_BRACKET_RE.sub('', text)
    # "слово  ." / "слово ," after note removal
    text = re.sub(r' +([,.;:!?…])', r'\1', text)
    text = re.sub(r' {2,}', ' ', text)
    return text


def clean_wiki_extract(extract: str) -> str:
    """Remove formula/image TextExtracts noise; keep readable Russian prose."""
    text = extract or ''
    text = _remove_balanced_tex_groups(text)
    text = _strip_indented_math_lines(text)
    text = _strip_bracket_notes(text)
    return _collapse_extract_whitespace(text)


def _headings_to_marked(extract: str) -> str:
    """Wrap == Heading == as marked spans for larger play UI (tokenize in_heading)."""
    from games.censorly.tokenize import HEADING_END, HEADING_START

    def repl(match: re.Match[str]) -> str:
        name = (match.group(2) or '').strip()
        if not name:
            return ''
        # No extra blank lines — body uses white-space:pre-wrap + block headings.
        return f'{HEADING_START}{name}{HEADING_END}'

    return _SECTION_HEADING_RE.sub(repl, extract or '')


def _trim_extract(extract: str) -> tuple[str, bool]:
    """Return (text, truncated). Prefer cutting before a section heading."""
    text = clean_wiki_extract(extract or '')
    text = _strip_tail_sections(text)
    text = _strip_orphan_heading(text)
    if len(text) <= MAX_BODY_CHARS:
        return _headings_to_marked(text), False

    cut = text[:MAX_BODY_CHARS]
    # Prefer ending just before the last full section heading in the window.
    best = -1
    for match in _SECTION_HEADING_RE.finditer(cut):
        if match.start() >= MIN_BODY_CHARS:
            best = match.start()
    if best >= MIN_BODY_CHARS:
        trimmed = cut[:best].rstrip()
    else:
        trimmed = cut
        for sep in ('\n\n', '\n', '. '):
            idx = trimmed.rfind(sep)
            if idx >= MIN_BODY_CHARS:
                trimmed = trimmed[: idx + len(sep)].rstrip()
                break
        else:
            trimmed = trimmed.rstrip()
    trimmed = _strip_orphan_heading(trimmed)
    return _headings_to_marked(trimmed), True


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
        # wiki headings (== Name ==) so we can strip tails / trim on sections.
        'exsectionformat': 'wiki',
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
    extract, truncated = _trim_extract(extract)
    if len(extract) < MIN_BODY_CHARS:
        raise WikiFetchError('Статья слишком короткая после очистки')
    return WikiArticle(
        title=resolved_title,
        pageid=int(page['pageid']),
        extract=extract,
        truncated=truncated,
    )
