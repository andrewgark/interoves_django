"""Create permanent random / URL-based Цензурки games."""

from __future__ import annotations

import json
import random

from django.db import IntegrityError, transaction

from games.censorly import CENSORLY_CHECKER_ID, CENSORLY_GAME_ID, CENSORLY_TAGS_KEY, CENSORLY_TASK_TYPE
from games.censorly.article_pool import load_article_pool
from games.censorly.tokenize import build_puzzle_payload
from games.censorly.wiki import WikiArticle, WikiFetchError, fetch_article, title_from_user_input
from games.models import CheckerType, Game, GameTaskGroup, RandomCensorlyGame, Task, TaskGroup
from games.placement_share import allocate_share_hash


class EmptyCensorlyPool(LookupError):
    """Article pool file has no titles."""


class CensorlyPoolExhausted(LookupError):
    """Every pool title already has a permanent game (or failed fetch)."""


def _get_game() -> Game:
    return Game.objects.get(pk=CENSORLY_GAME_ID)


def _create_from_article(article: WikiArticle, *, game: Game | None = None) -> RandomCensorlyGame:
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
    )
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
            label=f'censorly:random:{article.title}',
            checker=checker,
            points=1,
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
            points=1,
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
            name=f'Цензурка: {article.title}',
            share_hash=share_hash,
        )
        return row


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
    # Also match by resolved title after fetch
    article = fetch_article(title)
    return _create_from_article(article)


def get_or_create_random_game(*, max_attempts: int = 12) -> RandomCensorlyGame:
    """Pick a random unused pool title, fetch wiki, create game."""
    pool = load_article_pool()
    if not pool:
        raise EmptyCensorlyPool('Пул статей Цензурок пуст')

    game = _get_game()
    used = set(RandomCensorlyGame.objects.values_list('wiki_title', flat=True))
    available = [t for t in pool if t not in used]
    if not available:
        raise CensorlyPoolExhausted('Все статьи из пула уже использованы')

    random.shuffle(available)
    errors: list[str] = []
    for title in available[:max_attempts]:
        try:
            article = fetch_article(title)
            return _create_from_article(article, game=game)
        except WikiFetchError as exc:
            errors.append(f'{title}: {exc}')
            continue
        except IntegrityError:
            existing = (
                RandomCensorlyGame.objects.filter(wiki_title=title)
                .select_related('task_group')
                .first()
            )
            if existing is not None:
                return existing
            raise
    detail = '; '.join(errors[:3]) if errors else 'нет доступных статей'
    raise CensorlyPoolExhausted(f'Не удалось создать цензурку ({detail})')


def puzzle_json_size(task: Task) -> int:
    tags = task.tags if isinstance(task.tags, dict) else {}
    payload = tags.get(CENSORLY_TAGS_KEY)
    if not payload:
        return 0
    return len(json.dumps(payload, ensure_ascii=False))
