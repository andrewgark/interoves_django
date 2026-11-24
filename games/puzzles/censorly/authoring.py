"""Build a Цензурка puzzle from a Wikipedia article."""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from games.puzzles.censorly.article.wiki import WikiArticle, WikiFetchError, fetch_article
from games.puzzles.censorly.text.tokenize import build_puzzle_payload, title_content_lemmas


class CensorlyAuthoringError(ValueError):
    """The article cannot be a Цензурка."""


def puzzle_from_article(article: WikiArticle, *, pool_title: str | None = None) -> dict[str, Any]:
    """The puzzle payload stored in Task.tags; raises when the title has nothing to guess."""
    puzzle = build_puzzle_payload(
        wiki_title=article.title,
        body_text=article.extract,
        wiki_pageid=article.pageid,
        wiki_revid=getattr(article, 'revid', None),
        fetched_at=timezone.now().isoformat(),
        truncated=bool(getattr(article, 'truncated', False)),
    )
    if pool_title and pool_title != article.title:
        puzzle['source_pool_title'] = pool_title
    if not title_content_lemmas(puzzle):
        raise CensorlyAuthoringError('В названии нет угадываемых слов')
    return puzzle


def puzzle_for_title(title_or_url: str) -> tuple[str, dict[str, Any]]:
    """(article title, puzzle) for a Wikipedia title or link."""
    try:
        article = fetch_article(title_or_url)
    except WikiFetchError as exc:
        raise CensorlyAuthoringError(str(exc)) from exc
    return article.title, puzzle_from_article(article)
