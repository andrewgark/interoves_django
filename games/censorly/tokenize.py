"""Tokenize Wikipedia plaintext into censorly tokens."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from games.censorly.normalize import lemma_of, normalize_surface, split_stem_ending
from games.censorly.stopwords import is_stop_word

# Private-use markers wrap wiki section titles after fetch (see wiki._headings_to_marked).
HEADING_START = '\ufdd0'
HEADING_END = '\ufdd1'

# Combining marks stay with the letter so accents remain in `surface`.
_TOKEN_RE = re.compile(
    r'[A-Za-zА-Яа-яЁё0-9\u0300-\u036f]+(?:-[A-Za-zА-Яа-яЁё0-9\u0300-\u036f]+)*|[^\s\w]+|\s+',
    re.UNICODE,
)

_WORD_CORE_RE = re.compile(
    r'^[A-Za-zА-Яа-яЁё0-9\u0300-\u036f]+(?:-[A-Za-zА-Яа-яЁё0-9\u0300-\u036f]+)*$',
    re.UNICODE,
)

_HEADING_SPLIT_RE = re.compile(
    re.escape(HEADING_START) + r'(.*?)' + re.escape(HEADING_END),
    re.DOTALL,
)


def letter_length(surface: str) -> int:
    """Count letters/digits for mask width; ignore combining marks and hyphens."""
    n = 0
    for ch in unicodedata.normalize('NFC', surface or ''):
        if unicodedata.category(ch) == 'Mn':
            continue
        if ch == '-':
            continue
        if ch.isalnum():
            n += 1
    return n


def _kind_for_word(surface: str) -> str:
    # Strip combining marks before stop-word check.
    plain = ''.join(
        ch for ch in unicodedata.normalize('NFC', surface)
        if unicodedata.category(ch) != 'Mn'
    )
    if is_stop_word(plain):
        return 'stop'
    return 'content'


def _tokenize_chunk(text: str, *, in_title: bool, start_id: int) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    tid = start_id
    for match in _TOKEN_RE.finditer(unicodedata.normalize('NFC', text or '')):
        surface = match.group(0)
        if not surface:
            continue
        if surface.isspace():
            kind = 'space'
            lemma = ''
            length = 0
            ending = ''
            stem_length = 0
        elif _WORD_CORE_RE.fullmatch(surface):
            kind = _kind_for_word(surface)
            plain = ''.join(
                ch for ch in surface if unicodedata.category(ch) != 'Mn'
            )
            lemma = lemma_of(plain) if kind == 'content' else normalize_surface(plain)
            length = letter_length(surface)
            ending = ''
            stem_length = length
            if kind == 'content':
                _stem, ending = split_stem_ending(surface)
                stem_length = letter_length(_stem) if ending else length
        else:
            kind = 'punct'
            lemma = ''
            length = 0
            ending = ''
            stem_length = 0
        tok: dict[str, Any] = {
            'id': tid,
            'surface': surface,
            'kind': kind,
            'lemma': lemma,
            'length': length,
            'in_title': bool(in_title),
        }
        if kind == 'content' and ending:
            tok['ending'] = ending
            tok['stem_length'] = stem_length
        tokens.append(tok)
        tid += 1
    return tokens


def tokenize_text(text: str, *, in_title: bool = False, start_id: int = 0) -> list[dict[str, Any]]:
    """Split plaintext into tokens with lemma/kind/length; wiki headings → kind=heading."""
    raw = unicodedata.normalize('NFC', text or '')
    tokens: list[dict[str, Any]] = []
    tid = start_id
    pos = 0
    for match in _HEADING_SPLIT_RE.finditer(raw):
        if match.start() > pos:
            chunk = _tokenize_chunk(raw[pos:match.start()], in_title=in_title, start_id=tid)
            tokens.extend(chunk)
            tid = tid + len(chunk)
        heading = (match.group(1) or '').strip()
        if heading:
            tokens.append({
                'id': tid,
                'surface': heading,
                'kind': 'heading',
                'lemma': '',
                'length': 0,
                'in_title': False,
            })
            tid += 1
        pos = match.end()
    if pos < len(raw):
        chunk = _tokenize_chunk(raw[pos:], in_title=in_title, start_id=tid)
        tokens.extend(chunk)
    elif pos == 0 and not tokens:
        tokens = _tokenize_chunk(raw, in_title=in_title, start_id=start_id)
    return tokens


def build_puzzle_payload(
    *,
    wiki_title: str,
    body_text: str,
    wiki_pageid: int | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    title_tokens = tokenize_text(wiki_title, in_title=True, start_id=0)
    body_tokens = tokenize_text(body_text, in_title=False, start_id=len(title_tokens))
    return {
        'wiki_title': wiki_title,
        'wiki_pageid': wiki_pageid,
        'title_tokens': title_tokens,
        'body_tokens': body_tokens,
        'truncated': bool(truncated),
    }


def all_tokens(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload.get('title_tokens') or []) + list(payload.get('body_tokens') or [])


def title_content_lemmas(payload: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for tok in payload.get('title_tokens') or []:
        if tok.get('kind') == 'content' and tok.get('lemma'):
            out.add(tok['lemma'])
    return out


def ordered_title_lemmas(payload: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for tok in payload.get('title_tokens') or []:
        if tok.get('kind') != 'content':
            continue
        lemma = tok.get('lemma') or ''
        if lemma and lemma not in seen:
            seen.append(lemma)
    return seen
