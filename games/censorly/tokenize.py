"""Tokenize Wikipedia plaintext into censorly tokens."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from games.censorly import CENSORLY_SPLITTER_VERSION
from games.censorly.normalize import (
    WORD_CHARS,
    lemma_of,
    normalize_surface,
    split_stem_ending,
    strip_combining_marks,
)
from games.censorly.stopwords import is_stop_word

# Private-use markers wrap wiki section titles after fetch (see wiki._headings_to_marked).
# Level is carried as "{level}\x1f{name}" so "==" / "===" stay distinct. No separator
# means an older marker: treat it as a section (level 2).
HEADING_START = '\ufdd0'
HEADING_END = '\ufdd1'
HEADING_LEVEL_SEP = '\x1f'
# A display formula is one visible punct token, not a row of letter masks.
FORMULA_START = '\ufdd2'
FORMULA_END = '\ufdd3'

_MATH_SERVICE_SYMBOLS = '∫∬∭∮≤≥≠≈∈∉∑∏∂∇×·±∓→←↔∪∩∧∨√∞ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ'

# Greek, CJK, Cyrillic and Latin are words. Hyphen, ² and ₂ are not in the span.
_TOKEN_RE = re.compile(
    rf'[{WORD_CHARS}]+|[{re.escape(_MATH_SERVICE_SYMBOLS)}]|[^{WORD_CHARS}\s]+|\s+',
)

_WORD_CORE_RE = re.compile(
    rf'^[{WORD_CHARS}]+$',
)

_HEADING_SPLIT_RE = re.compile(
    re.escape(HEADING_START) + r'(.*?)' + re.escape(HEADING_END),
    re.DOTALL,
)
_FORMULA_SPLIT_RE = re.compile(
    re.escape(FORMULA_START) + r'(.*?)' + re.escape(FORMULA_END),
    re.DOTALL,
)


def _strip_invisible(text: str) -> str:
    """Drop format characters that split a word (soft hyphen, ZWSP, BOM)."""
    return ''.join(
        ch for ch in (text or '')
        if unicodedata.category(ch) != 'Cf'
    )


def letter_length(surface: str) -> int:
    """Mask width: word-span characters, without combining marks."""
    n = 0
    for ch in unicodedata.normalize('NFC', surface or ''):
        if unicodedata.category(ch) == 'Mn':
            continue
        if _WORD_CORE_RE.fullmatch(ch):
            n += 1
    return n


def _kind_for_word(surface: str) -> str:
    plain = strip_combining_marks(surface)
    if is_stop_word(plain):
        return 'stop'
    return 'content'


def _split_heading_marker(raw: str) -> tuple[int, str]:
    """Return (level 2–6, title). Missing separator keeps the whole string at level 2."""
    text = strip_combining_marks((raw or '').strip())
    level = 2
    if HEADING_LEVEL_SEP in text:
        level_s, name = text.split(HEADING_LEVEL_SEP, 1)
        if level_s.isdigit():
            level = int(level_s)
        text = strip_combining_marks(name.strip())
    return min(max(level, 2), 6), text


def _heading_break(tid: int) -> dict[str, Any]:
    return {
        'id': tid,
        'surface': '',
        'kind': 'heading_break',
        'lemma': '',
        'length': 0,
        'in_title': False,
        'in_heading': False,
    }


def _formula_token(surface: str, tid: int, *, in_title: bool, in_heading: bool) -> dict[str, Any]:
    return {
        'id': tid,
        'surface': surface,
        'kind': 'punct',
        'lemma': '',
        'length': 0,
        'in_title': bool(in_title),
        'in_heading': bool(in_heading),
    }


def _tokenize_chunk(
    text: str,
    *,
    in_title: bool,
    start_id: int,
    in_heading: bool = False,
) -> list[dict[str, Any]]:
    """Words, spaces, punctuation, and whole formulas as one visible token."""
    tokens: list[dict[str, Any]] = []
    tid = start_id
    pos = 0
    for match in _FORMULA_SPLIT_RE.finditer(text or ''):
        if match.start() > pos:
            chunk = _tokenize_plain(
                text[pos:match.start()],
                in_title=in_title,
                start_id=tid,
                in_heading=in_heading,
            )
            tokens.extend(chunk)
            tid += len(chunk)
        surface = re.sub(r'\s+', ' ', match.group(1) or '').strip()
        if surface:
            tokens.append(_formula_token(
                surface, tid, in_title=in_title, in_heading=in_heading,
            ))
            tid += 1
        pos = match.end()
    if pos < len(text or ''):
        tokens.extend(_tokenize_plain(
            (text or '')[pos:],
            in_title=in_title,
            start_id=tid,
            in_heading=in_heading,
        ))
    elif pos == 0:
        tokens.extend(_tokenize_plain(
            text or '',
            in_title=in_title,
            start_id=tid,
            in_heading=in_heading,
        ))
    return tokens


def _tokenize_plain(
    text: str,
    *,
    in_title: bool,
    start_id: int,
    in_heading: bool = False,
) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    tid = start_id
    cleaned = unicodedata.normalize('NFC', _strip_invisible(text))
    for match in _TOKEN_RE.finditer(cleaned):
        raw = match.group(0)
        if not raw:
            continue
        # Drop combining accents from play surfaces (guessing / display).
        surface = raw if raw.isspace() else strip_combining_marks(raw)
        if not surface and not raw.isspace():
            continue
        if surface.isspace():
            kind = 'space'
            lemma = ''
            length = 0
            ending = ''
            stem_length = 0
        elif _WORD_CORE_RE.fullmatch(raw) or _WORD_CORE_RE.fullmatch(surface):
            kind = _kind_for_word(surface)
            plain = strip_combining_marks(surface)
            lemma = lemma_of(plain) if kind == 'content' else normalize_surface(plain)
            length = letter_length(surface)
            ending = ''
            stem_length = length
            if kind == 'content':
                _stem, ending = split_stem_ending(surface)
                stem_length = letter_length(_stem) if ending else length
        else:
            kind = 'stop' if is_stop_word(surface) else 'punct'
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
            'in_heading': bool(in_heading),
        }
        if kind == 'content' and ending:
            tok['ending'] = ending
            tok['stem_length'] = stem_length
        tokens.append(tok)
        tid += 1
    return tokens


def tokenize_text(text: str, *, in_title: bool = False, start_id: int = 0) -> list[dict[str, Any]]:
    """Split plaintext into tokens; wiki headings → word tokens with in_heading."""
    raw = unicodedata.normalize('NFC', text or '')
    tokens: list[dict[str, Any]] = []
    tid = start_id
    pos = 0
    for match in _HEADING_SPLIT_RE.finditer(raw):
        if match.start() > pos:
            chunk = _tokenize_chunk(raw[pos:match.start()], in_title=in_title, start_id=tid)
            tokens.extend(chunk)
            tid = tid + len(chunk)
        level, heading = _split_heading_marker(match.group(1) or '')
        if heading:
            tokens.append(_heading_break(tid))
            tid += 1
            chunk = _tokenize_chunk(
                heading, in_title=False, start_id=tid, in_heading=True,
            )
            for tok in chunk:
                tok['heading_level'] = level
            tokens.extend(chunk)
            tid = tid + len(chunk)
            tokens.append(_heading_break(tid))
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
    wiki_revid: int | None = None,
    fetched_at: str | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    """Snapshot the cleaned article and derive tokens with the current splitter.

    ``body_text`` is the source of truth. Tokens are a cache stamped with
    ``splitter_version`` and are rebuilt when that version changes.
    """
    title_tokens = tokenize_text(wiki_title, in_title=True, start_id=0)
    body_tokens = tokenize_text(body_text, in_title=False, start_id=len(title_tokens))
    return {
        'wiki_title': wiki_title,
        'wiki_pageid': wiki_pageid,
        'wiki_revid': wiki_revid,
        'fetched_at': fetched_at,
        'body_text': body_text,
        'title_tokens': title_tokens,
        'body_tokens': body_tokens,
        'truncated': bool(truncated),
        'splitter_version': CENSORLY_SPLITTER_VERSION,
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


def _strip_token_surface(tok: dict[str, Any]) -> dict[str, Any]:
    surface = tok.get('surface') or ''
    out = tok
    if surface and not surface.isspace():
        plain = strip_combining_marks(surface)
        if plain != surface:
            out = dict(tok)
            out['surface'] = plain
    # Drop noisy endings stored before the allowlist filter.
    ending = (out.get('ending') or '').strip()
    if ending:
        from games.censorly.normalize import is_hintable_ending
        stem = ''
        try:
            stem_len = int(out.get('stem_length') or 0)
        except (TypeError, ValueError):
            stem_len = 0
        if stem_len > 0:
            stem = 'x' * stem_len
        if not is_hintable_ending(ending, stem=stem):
            if out is tok:
                out = dict(tok)
            out.pop('ending', None)
            out.pop('stem_length', None)
    return out


def _expand_legacy_heading(tok: dict[str, Any], start_id: int) -> list[dict[str, Any]]:
    """Turn one kind=heading blob into break + maskable heading words + break."""
    surface = strip_combining_marks((tok.get('surface') or '').strip())
    if not surface:
        return []
    out: list[dict[str, Any]] = [_heading_break(start_id)]
    tid = start_id + 1
    chunk = _tokenize_chunk(surface, in_title=False, start_id=tid, in_heading=True)
    for tok in chunk:
        tok['heading_level'] = 2
    out.extend(chunk)
    tid = tid + len(chunk)
    out.append(_heading_break(tid))
    return out


def upgrade_puzzle_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize stored puzzles: strip accents, expand legacy heading blobs.

    Ids are reassigned sequentially so expand stays consistent. Callers must not
    persist player state keyed only by old heading token ids (hints on headings
    were never allowed for title lemmas; body heading hints are rare).
    """
    if not isinstance(payload, dict):
        return payload
    changed = False
    new_title: list[dict[str, Any]] = []
    new_body: list[dict[str, Any]] = []
    for key, dest in (('title_tokens', new_title), ('body_tokens', new_body)):
        for tok in payload.get(key) or []:
            if not isinstance(tok, dict):
                continue
            if tok.get('kind') == 'heading':
                dest.extend(_expand_legacy_heading(tok, start_id=0))
                changed = True
                continue
            stripped = _strip_token_surface(tok)
            if stripped is not tok:
                changed = True
            if 'in_heading' not in stripped and stripped.get('kind') != 'heading_break':
                stripped = dict(stripped)
                stripped['in_heading'] = False
            dest.append(stripped)
    if not changed and all(
        'in_heading' in t or t.get('kind') == 'heading_break'
        for t in (payload.get('title_tokens') or []) + (payload.get('body_tokens') or [])
        if isinstance(t, dict)
    ):
        return payload
    # Reassign ids so expand stays unique and sequential.
    tid = 0
    for tok in new_title + new_body:
        tok['id'] = tid
        tid += 1
    out = dict(payload)
    out['title_tokens'] = new_title
    out['body_tokens'] = new_body
    return out


def _join_surfaces(tokens: list[dict[str, Any]]) -> str:
    return ''.join((tok.get('surface') or '') for tok in tokens)


def reconstruct_body_text(tokens: list[dict[str, Any]]) -> str:
    """Rebuild cleaned article text from stored tokens.

    Heading breaks become the same marked headings ``tokenize_text`` reads.
    Used when an older puzzle saved tokens and not ``body_text``.
    """
    parts: list[str] = []
    index = 0
    total = len(tokens)
    while index < total:
        tok = tokens[index]
        if tok.get('kind') == 'heading_break':
            index += 1
            chunk: list[dict[str, Any]] = []
            level = 2
            while index < total and tokens[index].get('kind') != 'heading_break':
                chunk.append(tokens[index])
                try:
                    found = int(tokens[index].get('heading_level') or 0)
                except (TypeError, ValueError):
                    found = 0
                if 2 <= found <= 6:
                    level = found
                index += 1
            name = _join_surfaces(chunk).strip()
            if name:
                parts.append(
                    f'{HEADING_START}{level}{HEADING_LEVEL_SEP}{name}{HEADING_END}'
                )
            if index < total and tokens[index].get('kind') == 'heading_break':
                index += 1
            continue
        parts.append(tok.get('surface') or '')
        index += 1
    return ''.join(parts)


def _copy_snapshot_extras(source: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    alias = (source.get('source_pool_title') or '').strip()
    if alias:
        fresh['source_pool_title'] = alias
    return fresh


def resolve_puzzle_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return tokens for the current splitter without refetching Wikipedia.

    The cleaned title and body stay as stored. A missing body is rebuilt from
    the saved tokens. A matching ``splitter_version`` keeps the token cache.
    """
    if not isinstance(payload, dict):
        return payload
    if payload.get('title_tokens') is None and not isinstance(payload.get('body_text'), str):
        return payload
    version_ok = payload.get('splitter_version') == CENSORLY_SPLITTER_VERSION
    has_text = isinstance(payload.get('body_text'), str)
    if version_ok and has_text and payload.get('title_tokens') is not None:
        return upgrade_puzzle_payload(payload)
    if has_text:
        title = payload.get('wiki_title') or ''
        body = payload.get('body_text') or ''
        source = payload
    else:
        source = upgrade_puzzle_payload(payload)
        title = (source.get('wiki_title') or '').strip()
        if not title:
            title = _join_surfaces(source.get('title_tokens') or []).strip()
        body = reconstruct_body_text(source.get('body_tokens') or [])
    fresh = build_puzzle_payload(
        wiki_title=title,
        body_text=body,
        wiki_pageid=source.get('wiki_pageid'),
        wiki_revid=source.get('wiki_revid'),
        fetched_at=source.get('fetched_at'),
        truncated=bool(source.get('truncated')),
    )
    return _copy_snapshot_extras(source, fresh)
