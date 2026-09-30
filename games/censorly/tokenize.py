"""Tokenize Wikipedia plaintext into censorly tokens."""

from __future__ import annotations

import re
from typing import Any

from games.censorly.normalize import lemma_of, normalize_surface
from games.censorly.stopwords import is_stop_word

# Word (letters/digits/hyphen) or a run of non-word/non-space or whitespace.
_TOKEN_RE = re.compile(
    r'[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*|[^\s\w]+|\s+',
    re.UNICODE,
)


def _kind_for_word(surface: str) -> str:
    if is_stop_word(surface):
        return 'stop'
    return 'content'


def tokenize_text(text: str, *, in_title: bool = False, start_id: int = 0) -> list[dict[str, Any]]:
    """Split plaintext into tokens with lemma/kind/length."""
    tokens: list[dict[str, Any]] = []
    tid = start_id
    for match in _TOKEN_RE.finditer(text or ''):
        surface = match.group(0)
        if not surface:
            continue
        if surface.isspace():
            kind = 'space'
            lemma = ''
            length = 0
        elif re.fullmatch(r'[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*', surface):
            kind = _kind_for_word(surface)
            lemma = lemma_of(surface) if kind == 'content' else normalize_surface(surface)
            length = len(normalize_surface(surface).replace('-', '')) or len(surface)
        else:
            kind = 'punct'
            lemma = ''
            length = 0
        tokens.append({
            'id': tid,
            'surface': surface,
            'kind': kind,
            'lemma': lemma,
            'length': length,
            'in_title': bool(in_title),
        })
        tid += 1
    return tokens


def build_puzzle_payload(
    *,
    wiki_title: str,
    body_text: str,
    wiki_pageid: int | None = None,
) -> dict[str, Any]:
    title_tokens = tokenize_text(wiki_title, in_title=True, start_id=0)
    body_tokens = tokenize_text(body_text, in_title=False, start_id=len(title_tokens))
    return {
        'wiki_title': wiki_title,
        'wiki_pageid': wiki_pageid,
        'title_tokens': title_tokens,
        'body_tokens': body_tokens,
    }


def all_tokens(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload.get('title_tokens') or []) + list(payload.get('body_tokens') or [])


def title_content_lemmas(payload: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for tok in payload.get('title_tokens') or []:
        if tok.get('kind') == 'content' and tok.get('lemma'):
            out.add(tok['lemma'])
    return out
