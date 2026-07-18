# Карточки разделов на главной (лесенки, замены, стены, палиндромы, десяточки).

from __future__ import annotations

from zoneinfo import ZoneInfo

from django.utils import timezone

from games.ladder_daily import (
    LADDER_GAME_ID,
    filter_published_ladder_links,
    get_ladder_hub_context,
)

MOSCOW = ZoneInfo('Europe/Moscow')

SECTION_HUB_ORDER = ('ladder', 'replacements', 'walls', 'palindromes')

SECTION_HUB_META = {
    'ladder': {
        'icon': '🪜',
        'title': 'Лесенка',
        'description': 'Одна лестница слов в день — соберите цепочку с двух концов.',
        'cta_today': 'Сегодняшняя лесенка',
        'cta_latest': 'Последняя лесенка',
        'all_link_label': 'Все лесенки →',
    },
    'replacements': {
        'icon': '🔄',
        'title': 'Замены',
        'description': 'Восстановите заменённые слова в тексте.',
        'cta_latest': 'Последние замены',
        'all_link_label': 'Все замены →',
    },
    'walls': {
        'icon': '🧱',
        'title': 'Стены',
        'description': 'Поделите 16 объектов на 4 категории по 4 объекта',
        'cta_latest': 'Последняя стена',
        'all_link_label': 'Все стены →',
    },
    'palindromes': {
        'icon': '🪞',
        'title': 'Палиндромы',
        'description': 'Восстановите палиндром.',
        'cta_latest': 'Последний палиндром',
        'all_link_label': 'Все палиндромы →',
    },
}

DESYATOCHKI_HUB_META = {
    'icon': '🔟',
    'title': 'Десяточки',
    'description': 'Командные сложные игры, в которых можно пользоваться интернетом',
    'cta_today': 'Сегодняшняя Десяточка',
    'cta_latest': 'Последняя Десяточка',
    'all_link_label': 'Все десяточки →',
}


def _newest_task_group_links(game):
    """Опубликованные круги раздела, новые сверху."""
    from games.models import GameTaskGroup

    qs = (
        GameTaskGroup.objects.filter(game=game)
        .select_related('task_group')
    )
    if game.id == LADDER_GAME_ID:
        qs = filter_published_ladder_links(qs, game)
    return GameTaskGroup.order_queryset_by_number(qs, reverse=True)


def get_training_section_hub_context(game):
    """Контекст карточки раздела (замены, стены, палиндромы)."""
    meta = SECTION_HUB_META[game.id]
    links = list(_newest_task_group_links(game))
    cta_number = links[0].number if links else None
    play_url = f'/games/{game.id}/{cta_number}/' if cta_number else None
    return {
        'id': game.id,
        'icon': meta['icon'],
        'title': meta['title'],
        'description': meta['description'],
        'cta_label': meta['cta_latest'] if cta_number else '',
        'cta_number': cta_number,
        'is_today': False,
        'play_url': play_url,
        'section_url': f'/section/{game.id}/',
        'all_link_label': meta['all_link_label'],
        'status': 'latest' if cta_number else 'empty',
        'game': game,
    }


def get_ladder_section_hub_card(game, *, published_numbers, now=None):
    """Карточка лесенки на главной из get_ladder_hub_context."""
    meta = SECTION_HUB_META[LADDER_GAME_ID]
    ctx = get_ladder_hub_context(game, published_numbers=published_numbers, now=now)
    cta_label = ctx.get('ladder_cta_label') or ''
    is_today = ctx.get('ladder_is_today', False)
    if is_today:
        cta_label = meta['cta_today']
    elif ctx.get('ladder_cta_number'):
        cta_label = meta['cta_latest']
    return {
        'id': LADDER_GAME_ID,
        'icon': meta['icon'],
        'title': meta['title'],
        'description': meta['description'],
        'cta_label': cta_label,
        'cta_number': ctx.get('ladder_cta_number'),
        'is_today': is_today,
        'play_url': ctx.get('ladder_play_url'),
        'section_url': ctx.get('ladder_section_url'),
        'all_link_label': meta['all_link_label'],
        'status': ctx.get('ladder_status', 'empty'),
        'today_label': ctx.get('ladder_today_label'),
        'game': game,
    }


def _first_announced_desyatochka(games, *, now=None):
    """Ближайшая публично видимая игра, чей start_time ещё не наступил."""
    now = now or timezone.now()
    for game in games:
        if now < game.start_time:
            return game
    return None


def _latest_started_desyatochka(games, *, now=None):
    """Самая новая игра, которая уже началась (доступна по прямому URL)."""
    now = now or timezone.now()
    for game in games:
        if now >= game.start_time:
            return game
    return None


def get_desyatochki_hub_context(games, *, now=None):
    """Карточка десяточек: последняя/сегодняшняя доступная игра по start_time."""
    meta = DESYATOCHKI_HUB_META
    now = now or timezone.now()
    if not games:
        return {
            'icon': meta['icon'],
            'title': meta['title'],
            'description': meta['description'],
            'cta_label': '',
            'is_today': False,
            'play_url': None,
            'section_url': '/games/',
            'all_link_label': meta['all_link_label'],
            'status': 'empty',
            'announced_game': None,
            'announced_games': [],
        }

    announced_game = _first_announced_desyatochka(games, now=now)
    latest = _latest_started_desyatochka(games, now=now)
    if not latest:
        return {
            'icon': meta['icon'],
            'title': meta['title'],
            'description': meta['description'],
            'cta_label': '',
            'is_today': False,
            'play_url': None,
            'section_url': '/games/',
            'all_link_label': meta['all_link_label'],
            'status': 'empty',
            'announced_game': announced_game,
            'announced_games': [announced_game] if announced_game else [],
        }

    today_msk = now.astimezone(MOSCOW).date()
    start_msk = latest.start_time.astimezone(MOSCOW).date()
    is_today = start_msk == today_msk
    cta_label = meta['cta_today'] if is_today else meta['cta_latest']
    return {
        'icon': meta['icon'],
        'title': meta['title'],
        'description': meta['description'],
        'cta_label': cta_label,
        'is_today': is_today,
        'play_url': f'/games/{latest.id}/',
        'section_url': '/games/',
        'all_link_label': meta['all_link_label'],
        'status': 'today' if is_today else 'latest',
        'game': latest,
        'announced_game': announced_game,
        'announced_games': [announced_game] if announced_game else [],
    }

