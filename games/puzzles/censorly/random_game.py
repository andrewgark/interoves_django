"""Create permanent random / URL-based Цензурки games."""

from __future__ import annotations

import html
import json
import logging
import random

from django.db import IntegrityError, transaction
from django.utils import timezone

from games.puzzles.censorly.ids import (
    CENSORLY_CHECKER_ID,
    CENSORLY_GAME_ID,
    CENSORLY_TAGS_KEY,
    CENSORLY_TASK_TYPE,
)
from games.puzzles.censorly.play import CENSORLY_BASE_POINTS
from games.puzzles.censorly.text.tokenize import build_puzzle_payload, title_content_lemmas
from games.puzzles.censorly.article.wiki import (
    CensorlyArticleRejected,
    WikiArticle,
    WikiFetchError,
    WikipediaRequestFailed,
    fetch_article,
    title_from_user_input,
)
from games.models import CheckerType, Game, GameTaskGroup, RandomCensorlyGame, Task, TaskGroup
from games.sections.placement_share import allocate_share_hash

logger = logging.getLogger('application')


class EmptyCensorlyPool(LookupError):
    """Article pool file has no titles."""


class CensorlyPoolExhausted(LookupError):
    """Every pool title already has a permanent game (or failed fetch)."""


class NoMatchingCensorlyArticles(LookupError):
    """No active pool articles match the selected filters."""


class CensorlyPoolFetchFailed(CensorlyPoolExhausted):
    """Matching unused titles exist, but none produced a playable article."""


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
        wiki_revid=article.revid,
        fetched_at=timezone.now().isoformat(),
        truncated=bool(getattr(article, 'truncated', False)),
    )
    if not title_content_lemmas(puzzle):
        raise CensorlyArticleRejected('В названии нет угадываемых слов')
    if pool_title and pool_title != article.title:
        puzzle['source_pool_title'] = pool_title
    checker = CheckerType.objects.get(pk=CENSORLY_CHECKER_ID)
    try:
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
    except IntegrityError:
        # A concurrent request may have created the canonical article after
        # our initial lookup. The unique wiki_title constraint is the arbiter.
        existing = (
            RandomCensorlyGame.objects.filter(wiki_title=article.title)
            .select_related('task_group')
            .first()
        )
        if existing is not None:
            return existing
        raise


def used_censorly_titles() -> set[str]:
    """Titles already used by any Цензурка (daily + random), including pool aliases."""
    used: set[str] = set(
        RandomCensorlyGame.objects.values_list('wiki_title', flat=True)
    )
    for answer, tags in Task.objects.filter(
        task_type=CENSORLY_TASK_TYPE, is_removed=False,
    ).values_list('answer', 'tags'):
        if answer:
            used.add(str(answer).strip())
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


def get_or_create_random_game(*, max_attempts: int = 12, filters: dict | None = None) -> RandomCensorlyGame:
    """Pick a random unused pool title, fetch wiki, create game."""
    game = _get_game()
    used = _used_pool_titles()
    from games.puzzles.censorly.article.admin import active_pool_titles, pool_rows
    tagged = pool_rows()
    pool = active_pool_titles(tagged)
    if not pool:
        raise EmptyCensorlyPool('Пул статей Цензурок пуст')
    interactive_filters = filters is not None
    filters = filters or {}
    def selected_values(key):
        raw = filters.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple, set)):
            return set()
        return {value for value in raw if isinstance(value, str)}

    difficulties = selected_values('difficulties')
    categories = selected_values('categories')
    types = selected_values('types')
    filter_difficulty = interactive_filters or bool(filters.get('filter_difficulty'))
    filter_types = interactive_filters or bool(filters.get('filter_types'))
    filter_categories = interactive_filters or bool(filters.get('filter_categories'))

    def matches(title):
        row = tagged.get(title)
        # Old pool files may contain untagged titles. Keep them eligible for
        # unfiltered support generation, but never claim they match a filter.
        if row is not None and not row['enabled']:
            return False
        if filter_difficulty and (row is None or row['difficulty'] not in difficulties):
            return False
        if filter_categories and (row is None or not any(
            path == category or path.startswith(category + '/')
            for path in row['topics'] for category in categories
        )):
            return False
        if filter_types and (row is None or not row['types'] or not set(row['types']).issubset(types)):
            return False
        return True

    pool = [title for title in pool if matches(title)]
    if not pool:
        raise NoMatchingCensorlyArticles('По выбранным фильтрам нет статей')
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
            article = fetch_article(title, timeout=8)
        except CensorlyArticleRejected as exc:
            from games.puzzles.censorly.article.admin import mark_unplayable

            mark_unplayable(title, tagged.get(title))
            logger.warning(
                'event=censorly_pool_article_rejected title=%r reason=%s',
                title, exc,
            )
            errors.append(f'{title}: {exc}')
            continue
        except WikipediaRequestFailed as exc:
            _report_wikipedia_request_failure(title, exc)
            errors.append(f'{title}: {exc}')
            continue
        except WikiFetchError as exc:
            # Unknown validation failures are not enough evidence to disable a
            # curated title; keep it eligible and surface the details.
            _report_wikipedia_request_failure(title, exc)
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
        except CensorlyArticleRejected as exc:
            from games.puzzles.censorly.article.admin import mark_unplayable

            mark_unplayable(title, tagged.get(title))
            logger.warning(
                'event=censorly_pool_article_rejected title=%r reason=%s',
                title, exc,
            )
            errors.append(f'{title}: {exc}')
            continue
        except WikiFetchError as exc:
            # Unclassified puzzle-generation errors are logged for diagnosis,
            # but don't disable the pool title or report them as network faults.
            logger.exception(
                'event=censorly_pool_article_validation_failed title=%r reason=%s',
                title, exc,
            )
            errors.append(f'{title}: {exc}')
            continue
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
    raise CensorlyPoolFetchFailed(f'Не удалось создать цензурку ({detail})')


def _report_wikipedia_request_failure(title: str, exc: Exception) -> None:
    cause = exc.__cause__
    detail = '{}: {}'.format(type(cause).__name__, cause) if cause else str(exc)
    logger.warning(
        'event=censorly_wikipedia_request_failed title=%r error_type=%s detail=%s',
        title, type(cause or exc).__name__, detail,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    try:
        from games.ops.telegram.bot.alerts import publish_admin_alert

        escaped_title = html.escape(title[:255], quote=False)
        escaped_detail = html.escape(detail[:3000], quote=False)
        publish_admin_alert(
            alert='censorly_wikipedia_failure',
            dedupe_key='telegram.admin_alert:censorly_wikipedia_failure',
            payload={
                'text': (
                    '⚠️ <b>Не удалось загрузить статью для случайной Цензурки</b>\n\n'
                    'Статья: <b>{}</b>\n'
                    'Ошибка: <code>{}</code>\n'
                    'Детали: {}'
                ).format(escaped_title, html.escape(type(cause or exc).__name__), escaped_detail),
            },
        )
    except Exception:
        logger.exception('event=censorly_wikipedia_admin_alert_failed title=%r', title)


def puzzle_json_size(task: Task) -> int:
    tags = task.tags if isinstance(task.tags, dict) else {}
    payload = tags.get(CENSORLY_TAGS_KEY)
    if not payload:
        return 0
    return len(json.dumps(payload, ensure_ascii=False))
