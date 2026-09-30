"""Create permanent random / URL-based Цензурки games."""

from __future__ import annotations

import json
import random

from django.db import IntegrityError, transaction

from games.censorly import CENSORLY_CHECKER_ID, CENSORLY_GAME_ID, CENSORLY_TAGS_KEY, CENSORLY_TASK_TYPE
from games.censorly.article_pool import load_article_pool
from games.censorly.play import CENSORLY_BASE_POINTS
from games.censorly.tokenize import build_puzzle_payload, title_content_lemmas
from games.censorly.wiki import WikiArticle, WikiFetchError, fetch_article, title_from_user_input
from games.models import CheckerType, Game, GameTaskGroup, RandomCensorlyGame, Task, TaskGroup
from games.placement_share import allocate_share_hash


class EmptyCensorlyPool(LookupError):
    """Article pool file has no titles."""


class CensorlyPoolExhausted(LookupError):
    """Every pool title already has a permanent game (or failed fetch)."""


def _get_game() -> Game:
    return Game.objects.get(pk=CENSORLY_GAME_ID)


def _create_from_article(
    article: WikiArticle,
    *,
    game: Game | None = None,
    pool_title: str | None = None,
) -> RandomCensorlyGame:
    game = game or _get_game()
    existing = (
        RandomCensorlyGame.objects.filter(wiki_title=article.title)
        .select_related('task_group')
        .first()
    )
    if existing is not None:
        return existing

    puzzle = build_puzzle_payload(
        wiki_title=article.title,
        body_text=article.extract,
        wiki_pageid=article.pageid,
        truncated=bool(getattr(article, 'truncated', False)),
    )
    if not title_content_lemmas(puzzle):
        raise WikiFetchError('В названии нет угадываемых слов')
    if pool_title and pool_title != article.title:
        puzzle['source_pool_title'] = pool_title
    checker = CheckerType.objects.get(pk=CENSORLY_CHECKER_ID)
    with transaction.atomic():
        existing = (
            RandomCensorlyGame.objects.select_for_update()
            .filter(wiki_title=article.title)
            .first()
        )
        if existing is not None:
            return existing
        task_group = TaskGroup.objects.create(
            label=('censorly:random:' + article.title)[:100],
            checker=checker,
            points=CENSORLY_BASE_POINTS,
            max_attempts=None,
        )
        Task.objects.create(
            task_group=task_group,
            number='1',
            task_type=CENSORLY_TASK_TYPE,
            checker=checker,
            checker_data='',
            answer=article.title,
            text='',
            tags={CENSORLY_TAGS_KEY: puzzle},
            points=CENSORLY_BASE_POINTS,
            is_removed=False,
        )
        share_hash = allocate_share_hash()
        row = RandomCensorlyGame.objects.create(
            wiki_title=article.title,
            share_hash=share_hash,
            task_group=task_group,
        )
        GameTaskGroup.objects.create(
            game=game,
            task_group=task_group,
            number=share_hash,
            name=('Цензурка: ' + article.title)[:100],
            share_hash=share_hash,
        )
        return row


def used_censorly_titles() -> set[str]:
    """Titles already used by any Цензурка (daily + random), including pool aliases."""
    used: set[str] = set(
        RandomCensorlyGame.objects.values_list('wiki_title', flat=True)
    )
    for answer in Task.objects.filter(
        task_type=CENSORLY_TASK_TYPE, is_removed=False,
    ).exclude(answer='').values_list('answer', flat=True):
        if answer:
            used.add(str(answer).strip())
    for tags in Task.objects.filter(
        task_type=CENSORLY_TASK_TYPE, is_removed=False,
    ).values_list('tags', flat=True):
        if not isinstance(tags, dict):
            continue
        payload = tags.get(CENSORLY_TAGS_KEY) or {}
        if not isinstance(payload, dict):
            continue
        for key in ('wiki_title', 'source_pool_title'):
            alias = (payload.get(key) or '').strip()
            if alias:
                used.add(alias)
    return used


def _used_pool_titles() -> set[str]:
    """Compatibility alias for random pool selection."""
    return used_censorly_titles()


def create_from_title(title_or_url: str) -> RandomCensorlyGame:
    """Fetch Wikipedia article and create (or return existing) permanent game."""
    title = title_from_user_input(title_or_url)
    existing = (
        RandomCensorlyGame.objects.filter(wiki_title=title)
        .select_related('task_group')
        .first()
    )
    if existing is not None:
        return existing
    article = fetch_article(title)
    return _create_from_article(article, pool_title=title if title != article.title else None)


def get_or_create_random_game(*, max_attempts: int = 12) -> RandomCensorlyGame:
    """Pick a random unused pool title, fetch wiki, create game."""
    pool = load_article_pool()
    if not pool:
        raise EmptyCensorlyPool('Пул статей Цензурок пуст')

    game = _get_game()
    used = _used_pool_titles()
    available = [t for t in pool if t not in used]
    if not available:
        raise CensorlyPoolExhausted('Все статьи из пула уже использованы')

    random.shuffle(available)
    errors: list[str] = []
    tried = 0
    for title in available:
        if tried >= max_attempts:
            break
        tried += 1
        try:
            article = fetch_article(title)
        except WikiFetchError as exc:
            errors.append(f'{title}: {exc}')
            continue
        if article.title in used:
            # Pool alias redirects to an already-used article.
            used.add(title)
            continue
        if RandomCensorlyGame.objects.filter(wiki_title=article.title).exists():
            used.add(article.title)
            used.add(title)
            continue
        try:
            return _create_from_article(article, game=game, pool_title=title)
        except IntegrityError:
            existing = (
                RandomCensorlyGame.objects.filter(wiki_title=article.title)
                .select_related('task_group')
                .first()
            )
            if existing is not None:
                return existing
            used.add(article.title)
            used.add(title)
            continue
    detail = '; '.join(errors[:3]) if errors else 'нет доступных статей'
    raise CensorlyPoolExhausted(f'Не удалось создать цензурку ({detail})')


def puzzle_json_size(task: Task) -> int:
    tags = task.tags if isinstance(task.tags, dict) else {}
    payload = tags.get(CENSORLY_TAGS_KEY)
    if not payload:
        return 0
    return len(json.dumps(payload, ensure_ascii=False))
