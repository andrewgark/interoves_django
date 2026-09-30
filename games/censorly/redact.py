"""Build client-safe redacted views of a censorly puzzle."""

from __future__ import annotations

from typing import Any, Iterable

from games.censorly.normalize import strip_combining_marks
from games.censorly.tokenize import all_tokens, title_content_lemmas


def _token_public(
    tok: dict[str, Any],
    *,
    revealed_lemmas: set[str],
    last_lemma: str | None,
    won: bool,
    title_lemmas: set[str],
) -> dict[str, Any]:
    kind = tok.get('kind') or 'content'
    out: dict[str, Any] = {
        'id': tok['id'],
        'kind': kind,
        'in_title': bool(tok.get('in_title')),
    }
    if tok.get('title_color') is not None:
        out['title_color'] = int(tok['title_color'])
    if kind in ('space', 'punct', 'stop'):
        out['text'] = tok.get('surface') or ''
        out['revealed'] = True
        return out

    lemma = tok.get('lemma') or ''
    length = int(tok.get('length') or 0)
    out['length'] = length
    out['lemma'] = lemma
    if lemma and lemma in title_lemmas:
        out['title_lemma'] = True
    is_open = won or (lemma and lemma in revealed_lemmas)
    out['revealed'] = bool(is_open)
    if is_open:
        out['text'] = tok.get('surface') or ''
        out['just_revealed'] = bool(last_lemma and lemma == last_lemma)
    return out


def build_public_view(
    payload: dict[str, Any],
    *,
    revealed_lemmas: Iterable[str] | None = None,
    last_lemma: str | None = None,
    won: bool = False,
) -> dict[str, Any]:
    revealed = {str(x) for x in (revealed_lemmas or []) if x}
    title_lemmas = title_content_lemmas(payload)
    title = [
        _token_public(
            t, revealed_lemmas=revealed, last_lemma=last_lemma, won=won,
            title_lemmas=title_lemmas,
        )
        for t in (payload.get('title_tokens') or [])
    ]
    body = [
        _token_public(
            t, revealed_lemmas=revealed, last_lemma=last_lemma, won=won,
            title_lemmas=title_lemmas,
        )
        for t in (payload.get('body_tokens') or [])
    ]
    needed = title_lemmas
    opened_title = needed <= revealed if needed else won
    return {
        'title_tokens': title,
        'body_tokens': body,
        'title_complete': bool(won or opened_title),
        'wiki_title': payload.get('wiki_title') if won else None,
        'title_color_map': dict(payload.get('title_color_map') or {}),
    }


def lemmas_matching_guess(payload: dict[str, Any], guess_lemma: str, guess_norm: str) -> set[str]:
    """Return lemmas in the puzzle that match the guess (lemma or surface)."""
    hits: set[str] = set()
    if not guess_lemma and not guess_norm:
        return hits
    for tok in all_tokens(payload):
        if tok.get('kind') != 'content':
            continue
        lemma = tok.get('lemma') or ''
        surface_plain = strip_combining_marks(
            (tok.get('surface') or '').lower().replace('ё', 'е')
        )
        if guess_lemma and lemma == guess_lemma:
            hits.add(lemma)
        elif guess_norm and surface_plain == guess_norm:
            hits.add(lemma or guess_norm)
    return hits


def count_hits(payload: dict[str, Any], lemma: str) -> int:
    if not lemma:
        return 0
    return sum(
        1
        for tok in all_tokens(payload)
        if tok.get('kind') == 'content' and tok.get('lemma') == lemma
    )


def newly_revealed_ids(payload: dict[str, Any], lemma: str) -> list[int]:
    return [
        int(tok['id'])
        for tok in all_tokens(payload)
        if tok.get('kind') == 'content' and tok.get('lemma') == lemma
    ]


def token_by_id(payload: dict[str, Any], token_id: int) -> dict[str, Any] | None:
    for tok in all_tokens(payload):
        if int(tok.get('id', -1)) == int(token_id):
            return tok
    return None
