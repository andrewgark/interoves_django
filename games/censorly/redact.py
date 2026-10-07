"""Build client-safe redacted views of a censorly puzzle."""

from __future__ import annotations

from typing import Any, Iterable

from games.censorly import CENSORLY_SHOW_MASK_ENDINGS
from games.censorly.normalize import is_hintable_ending, strip_combining_marks
from games.censorly.tokenize import all_tokens, title_content_lemmas


def _token_public(
    tok: dict[str, Any],
    *,
    revealed_lemmas: set[str],
    last_lemma: str | None,
    won: bool,
    title_lemmas: set[str],
    show_endings: bool,
    lexical: bool = False,
) -> dict[str, Any]:
    kind = tok.get('kind') or 'content'
    out: dict[str, Any] = {
        'id': tok['id'],
        'kind': kind,
        'in_title': bool(tok.get('in_title')),
    }
    if tok.get('in_heading'):
        out['in_heading'] = True
        try:
            level = int(tok.get('heading_level') or 0)
        except (TypeError, ValueError):
            level = 0
        if 2 <= level <= 6:
            out['heading_level'] = level
    # Always-open structural tokens (legacy `heading` = whole section title blob).
    if kind in ('space', 'punct', 'stop', 'heading', 'heading_break'):
        out['text'] = tok.get('surface') or ''
        out['revealed'] = True
        return out

    if lexical:
        from games.censorly.lexical.semantics import initially_open
        if initially_open(tok.get('surface') or ''):
            out['text'] = strip_combining_marks(tok.get('surface') or '')
            out['revealed'] = True
            out['guessed'] = False
            return out

    lemma = tok.get('lemma') or ''
    length = int(tok.get('length') or 0)
    out['length'] = length
    if lemma and lemma in title_lemmas:
        out['title_lemma'] = True
    player_opened = bool(lemma and lemma in revealed_lemmas)
    is_open = won or player_opened
    out['revealed'] = bool(is_open)
    out['guessed'] = player_opened
    if is_open:
        out['lemma'] = lemma
        out['text'] = strip_combining_marks(tok.get('surface') or '')
        out['just_revealed'] = bool(last_lemma and lemma == last_lemma)
    elif show_endings:
        ending = (tok.get('ending') or '').strip()
        try:
            stem_len = int(tok.get('stem_length') or 0)
        except (TypeError, ValueError):
            stem_len = 0
        # Re-validate so older puzzles drop noisy one-letter / irregular tails.
        stem_guess = ''
        if stem_len > 0:
            stem_guess = 'x' * stem_len
        elif ending and length > len(ending):
            stem_guess = 'x' * (length - len(ending))
        if ending and is_hintable_ending(ending, stem=stem_guess):
            out['ending'] = ending
            if stem_len > 0:
                out['stem_length'] = stem_len
    return out


def build_public_view(
    payload: dict[str, Any],
    *,
    revealed_lemmas: Iterable[str] | None = None,
    last_lemma: str | None = None,
    won: bool = False,
    show_endings: bool | None = None,
) -> dict[str, Any]:
    from games.censorly.flags import lexical_resolver_enabled
    revealed = {str(x) for x in (revealed_lemmas or []) if x}
    title_lemmas = title_content_lemmas(payload)
    lexical = lexical_resolver_enabled()
    endings_on = CENSORLY_SHOW_MASK_ENDINGS if show_endings is None else bool(show_endings)
    title = [
        _token_public(
            t, revealed_lemmas=revealed, last_lemma=last_lemma, won=won,
            title_lemmas=title_lemmas, show_endings=endings_on, lexical=lexical,
        )
        for t in (payload.get('title_tokens') or [])
    ]
    body = [
        _token_public(
            t, revealed_lemmas=revealed, last_lemma=last_lemma, won=won,
            title_lemmas=title_lemmas, show_endings=endings_on, lexical=lexical,
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
        'show_mask_endings': endings_on,
    }


def lemmas_matching_guess(payload: dict[str, Any], guess_lemma: str, guess_norm: str) -> set[str]:
    """Return lemmas in the puzzle that match the guess (lemma or surface)."""
    from games.censorly.flags import lexical_resolver_enabled
    if lexical_resolver_enabled():
        from games.censorly.lexical.match import matching_lemmas
        return matching_lemmas(all_tokens(payload), guess_norm or guess_lemma)
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


_SHARE_RUN_LIMIT = 2800


def share_mask_runs(
    tokens: Iterable[dict[str, Any]] | None,
    *,
    first_paragraph: bool = False,
) -> list[dict[str, Any]]:
    """Unsolved play view: open stop/punct, content words as length masks.

    Paragraph breaks and section headings stay in the run list so the story
    card can keep going until the sheet is full. ``first_paragraph`` stops
    at the first break, for callers that only want the lead.
    """
    runs: list[dict[str, Any]] = []
    started = False
    budget = 0
    stop = False

    def push_text(text: str) -> None:
        nonlocal budget
        if not text:
            return
        if runs and runs[-1].get('kind') == 'text':
            runs[-1]['text'] += text
        else:
            runs.append({'kind': 'text', 'text': text})
        budget += len(text)

    def push_break() -> None:
        nonlocal stop
        if first_paragraph and started:
            stop = True
            return
        if not started:
            return
        if runs and runs[-1].get('kind') == 'text':
            trimmed = str(runs[-1].get('text') or '').rstrip()
            if trimmed:
                runs[-1]['text'] = trimmed
            else:
                runs.pop()
        if runs and runs[-1].get('kind') != 'break':
            runs.append({'kind': 'break'})

    for tok in tokens or []:
        if stop or budget >= _SHARE_RUN_LIMIT:
            break
        if not isinstance(tok, dict):
            continue
        kind = tok.get('kind') or ''
        if kind in ('heading_break', 'heading'):
            push_break()
            continue
        text = str(tok.get('text') or '')
        if '\n' in text:
            push_break()
            continue
        if kind == 'content' and not tok.get('revealed'):
            try:
                length = int(tok.get('length') or 0)
            except (TypeError, ValueError):
                length = 0
            if length <= 0:
                continue
            started = True
            run: dict[str, Any] = {'kind': 'mask', 'length': length}
            ending = str(tok.get('ending') or '').strip()
            if ending:
                run['ending'] = ending
            if tok.get('title_lemma'):
                run['title'] = True
            runs.append(run)
            budget += length
        elif text:
            if text.strip():
                started = True
            elif not started:
                continue
            push_text(text)
    while runs and runs[-1].get('kind') == 'break':
        runs.pop()
    while runs and runs[-1].get('kind') == 'text':
        trimmed = str(runs[-1].get('text') or '').rstrip()
        if trimmed == runs[-1].get('text'):
            break
        if trimmed:
            runs[-1]['text'] = trimmed
            break
        runs.pop()
    return runs


def unsolved_share_runs(puzzle: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Title and article body as they look before any guess.

    The body keeps later paragraphs and headings. The card draws as many
    lines as fit and ends with an ellipsis.
    """
    view = build_public_view(puzzle, revealed_lemmas=(), won=False)
    return (
        share_mask_runs(view.get('title_tokens')),
        share_mask_runs(view.get('body_tokens')),
    )
