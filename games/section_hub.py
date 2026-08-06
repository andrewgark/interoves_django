# Карточки разделов на главной (лесенки, замены, стены, палиндромы, десяточки).

from __future__ import annotations

import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from games.alphabetty_daily import (
    ALPHABETTY_GAME_ID,
    filter_published_alphabetty_links,
    get_alphabetty_hub_context,
)
from games.ladder_daily import (
    LADDER_GAME_ID,
    filter_published_ladder_links,
    get_ladder_hub_context,
)
from games.week_task_weekly import (
    WEEK_TASK_GAME_ID,
    filter_published_week_task_links,
    get_week_task_hub_context,
)
from games.section_paths import section_hub_path, section_last_path, section_play_path

_DES_GAME_ID_RE = re.compile(r'^des(\d+)$')

MOSCOW = ZoneInfo('Europe/Moscow')

SECTION_HUB_ORDER = ('ladder', 'alphabetty', 'replacements', 'walls', 'palindromes')

# Группы карточек на главной (порядок внутри группы).
HUB_DAILY_SECTION_IDS = ('ladder', 'alphabetty')
HUB_FROM_DESYATOCHKI_SECTION_IDS = ('week_task', 'replacements', 'walls', 'palindromes')
WEEK_TASK_HUB_ID = WEEK_TASK_GAME_ID

# ph_icon — имя Phosphor без префикса (класс: `ph ph-{name}`); emoji icon — legacy/share.
SECTION_HUB_META = {
    'ladder': {
        'icon': '🪜',
        'ph_icon': 'ladder',
        'title': 'Лесенки',
        'description': 'Одна лестница слов в день — соберите цепочку с двух концов.',
        'cta_today': 'Сегодняшняя лесенка',
        'cta_latest': 'Последняя лесенка',
        'all_link_label': 'Все лесенки →',
    },
    'alphabetty': {
        'icon': '🔤',
        'ph_icon': 'text-aa',
        'title': 'Алфавитки',
        'description': 'Угадайте существительное по алфавиту — раньше или позже.',
        'cta_today': 'Сегодняшняя алфавитка',
        'cta_latest': 'Последняя алфавитка',
        'all_link_label': 'Все алфавитки →',
    },
    'replacements': {
        'icon': '🔄',
        'ph_icon': 'swap',
        'title': 'Замены',
        'description': 'Восстановите заменённые слова в тексте.',
        'cta_latest': 'Последние замены',
        'all_link_label': 'Все замены →',
    },
    'walls': {
        'icon': '🧱',
        'ph_icon': 'wall',
        'title': 'Стены',
        'description': 'Поделите 16 объектов на 4 категории по 4 объекта',
        'cta_latest': 'Последняя стена',
        'all_link_label': 'Все стены →',
    },
    'palindromes': {
        'icon': '🪞',
        'ph_icon': 'arrows-in-line-horizontal',
        'title': 'Палиндромы',
        'description': 'Восстановите палиндром.',
        'cta_latest': 'Последний палиндром',
        'all_link_label': 'Все палиндромы →',
    },
    'week_task': {
        'icon': '⭐',
        'ph_icon': 'star',
        'title': 'Задание недели',
        'description': 'Избранное сложное задание из одной из предыдущих Десяточек',
        'cta_today': 'Задание этой недели',
        'cta_latest': 'Последнее задание недели',
        'all_link_label': 'Все задания недели →',
        'soon_text': 'В понедельник 3 августа в 00:00 МСК выйдет первое задание недели.',
        'soon_emphasis': True,
    },
}

DESYATOCHKI_HUB_META = {
    'icon': '🔟',
    'ph_icon': 'puzzle-piece',
    'title': 'Десяточки',
    'description': 'Командные сложные игры, в которых можно пользоваться интернетом',
    'cta_today': 'Сегодняшняя Десяточка',
    'cta_latest': 'Последняя Десяточка',
    'all_link_label': 'Все десяточки →',
}


def section_ph_icon(section_id):
    """Phosphor icon name for a hub/nav section id, or empty string."""
    meta = SECTION_HUB_META.get(section_id) or (
        DESYATOCHKI_HUB_META if section_id == 'desyatochki' else None
    )
    if not meta:
        return ''
    return meta.get('ph_icon') or ''


def _newest_task_group_links(game):
    """Опубликованные круги раздела, новые сверху."""
    from games.models import GameTaskGroup

    qs = (
        GameTaskGroup.objects.filter(game=game)
        .select_related('task_group')
    )
    if game.id == LADDER_GAME_ID:
        qs = filter_published_ladder_links(qs, game)
    elif game.id == ALPHABETTY_GAME_ID:
        qs = filter_published_alphabetty_links(qs, game)
    elif game.id == WEEK_TASK_GAME_ID:
        qs = filter_published_week_task_links(qs, game)
    return GameTaskGroup.order_queryset_by_number(qs, reverse=True)


def get_training_section_hub_context(game):
    """Контекст карточки раздела (замены, стены, палиндромы)."""
    meta = SECTION_HUB_META[game.id]
    links = list(_newest_task_group_links(game))
    cta_number = links[0].number if links else None
    play_url = section_last_path(game.id) if cta_number else None
    return {
        'id': game.id,
        'icon': meta['icon'],
        'ph_icon': meta.get('ph_icon', ''),
        'title': meta['title'],
        'description': meta['description'],
        'cta_label': meta['cta_latest'] if cta_number else '',
        'cta_number': cta_number,
        'is_today': False,
        'play_url': play_url,
        'section_url': section_hub_path(game.id),
        'all_link_label': meta['all_link_label'],
        'status': 'latest' if cta_number else 'empty',
        'game': game,
    }


def get_source_desyatka_context(task_group, *, team=None):
    """
    Исходная Десяточка для круга раздела (Замены / Стены / Палиндромы).

    Круги хаба делят TaskGroup с игрой desN — по этой связи и находим источник.
    Возвращает dict с url / label / answers_url или None.
    """
    from games.models import Attempt, GameTaskGroup
    from games.telegram.game_urls import game_answers_url, game_play_path

    if task_group is None:
        return None
    tg_id = getattr(task_group, 'pk', None) or getattr(task_group, 'id', None)
    if not tg_id:
        return None

    candidates = (
        GameTaskGroup.objects
        .filter(task_group_id=tg_id, game__id__startswith='des')
        .select_related('game')
        .order_by('game__id')
    )
    link = None
    number = None
    for cand in candidates:
        m = _DES_GAME_ID_RE.match(str(cand.game_id))
        if m:
            link = cand
            number = m.group(1)
            break
    if link is None or link.game is None:
        return None

    game = link.game
    label = 'Десяточки {}'.format(number) if number else (
        (game.outside_name or game.name or game.id or '').strip() or str(game.id)
    )
    answers_url = ''
    raw_answers = game_answers_url(game) or ''
    if raw_answers and game.has_access(
        'see_answer',
        team=team,
        attempt=Attempt(time=timezone.now()),
    ):
        answers_url = raw_answers

    return {
        'game': game,
        'number': number,
        'label': label,
        'url': game_play_path(game),
        'answers_url': answers_url,
    }


def get_week_task_section_hub_card(game, *, published_numbers, now=None):
    """Карточка «Задание недели» на главной."""
    meta = SECTION_HUB_META[WEEK_TASK_HUB_ID]
    ctx = get_week_task_hub_context(game, published_numbers=published_numbers, now=now)
    cta_label = ctx.get('week_task_cta_label') or ''
    is_today = ctx.get('week_task_is_today', False)
    if is_today:
        cta_label = meta['cta_today']
    elif ctx.get('week_task_cta_number'):
        cta_label = meta['cta_latest']
    status = ctx.get('week_task_status', 'empty')
    return {
        'id': WEEK_TASK_HUB_ID,
        'icon': meta['icon'],
        'ph_icon': meta.get('ph_icon', ''),
        'title': meta['title'],
        'description': meta['description'],
        'cta_label': cta_label,
        'cta_number': ctx.get('week_task_cta_number'),
        'is_today': is_today,
        'play_url': ctx.get('week_task_play_url'),
        'section_url': ctx.get('week_task_section_url'),
        'all_link_label': meta['all_link_label'],
        'status': status,
        'today_label': ctx.get('week_task_today_label'),
        'soon_text': meta.get('soon_text', ''),
        'soon_emphasis': bool(meta.get('soon_emphasis')),
        'game': game,
    }


def get_week_task_hub_card():
    """Совместимость: заглушка, если игры ещё нет."""
    meta = SECTION_HUB_META[WEEK_TASK_HUB_ID]
    return {
        'id': WEEK_TASK_HUB_ID,
        'icon': meta['icon'],
        'ph_icon': meta.get('ph_icon', ''),
        'title': meta['title'],
        'description': meta['description'],
        'cta_label': '',
        'cta_number': None,
        'is_today': False,
        'play_url': None,
        'section_url': section_hub_path(WEEK_TASK_HUB_ID),
        'all_link_label': '',
        'status': 'coming_soon',
        'soon_text': meta.get('soon_text', ''),
        'soon_emphasis': bool(meta.get('soon_emphasis')),
        'game': None,
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
        'ph_icon': meta.get('ph_icon', ''),
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


def get_alphabetty_section_hub_card(game, *, published_numbers, now=None):
    """Карточка Алфавитки на главной."""
    meta = SECTION_HUB_META[ALPHABETTY_GAME_ID]
    ctx = get_alphabetty_hub_context(game, published_numbers=published_numbers, now=now)
    cta_label = ctx.get('alphabetty_cta_label') or ''
    is_today = ctx.get('alphabetty_is_today', False)
    if is_today:
        cta_label = meta['cta_today']
    elif ctx.get('alphabetty_cta_number'):
        cta_label = meta['cta_latest']
    return {
        'id': ALPHABETTY_GAME_ID,
        'icon': meta['icon'],
        'ph_icon': meta.get('ph_icon', ''),
        'title': meta['title'],
        'description': meta['description'],
        'cta_label': cta_label,
        'cta_number': ctx.get('alphabetty_cta_number'),
        'is_today': is_today,
        'play_url': ctx.get('alphabetty_play_url'),
        'section_url': ctx.get('alphabetty_section_url'),
        'all_link_label': meta['all_link_label'],
        'status': ctx.get('alphabetty_status', 'empty'),
        'today_label': ctx.get('alphabetty_today_label'),
        'game': game,
    }


def _first_announced_desyatochka(games, *, now=None):
    """Ближайшая анонсированная десяточка для карточки на главной.

    Порядок выбора (как у публичного /des в Telegram):
    1) ближайшая будущая (ещё не стартовала);
    2) иначе идущая прямо сейчас;
    3) иначе закончившаяся менее суток назад;
    4) иначе None.
    """
    now = now or timezone.now()
    if not games:
        return None

    upcoming = [g for g in games if now < g.start_time]
    if upcoming:
        return min(upcoming, key=lambda g: (g.start_time, g.id))

    live = [g for g in games if g.start_time <= now <= g.end_time]
    if live:
        return max(live, key=lambda g: (g.start_time, g.id))

    day = timedelta(days=1)
    recent = [
        g for g in games
        if now > g.end_time and (now - g.end_time) < day
    ]
    if recent:
        return max(recent, key=lambda g: (g.end_time, g.id))

    return None


def _latest_started_desyatochka(games, *, now=None):
    """Самая новая игра, которая уже началась (доступна по прямому URL)."""
    now = now or timezone.now()
    for game in games:
        if now >= game.start_time:
            return game
    return None


def get_desyatochki_hub_context(games, *, now=None, base=''):
    """Карточка десяточек: последняя/сегодняшняя доступная игра по start_time.

    ``base`` — префикс проекта (например ``/glowbyte``); пустой для главной.
    """
    meta = DESYATOCHKI_HUB_META
    now = now or timezone.now()
    games_url = (base + '/games/') if base else '/games/'
    if not games:
        return {
            'icon': meta['icon'],
            'ph_icon': meta.get('ph_icon', ''),
            'title': meta['title'],
            'description': meta['description'],
            'cta_label': '',
            'is_today': False,
            'play_url': None,
            'section_url': games_url,
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
            'ph_icon': meta.get('ph_icon', ''),
            'title': meta['title'],
            'description': meta['description'],
            'cta_label': '',
            'is_today': False,
            'play_url': None,
            'section_url': games_url,
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
        'ph_icon': meta.get('ph_icon', ''),
        'title': meta['title'],
        'description': meta['description'],
        'cta_label': cta_label,
        'is_today': is_today,
        'play_url': f'{games_url}{latest.id}/',
        'section_url': games_url,
        'all_link_label': meta['all_link_label'],
        'status': 'today' if is_today else 'latest',
        'game': latest,
        'announced_game': announced_game,
        'announced_games': [announced_game] if announced_game else [],
    }

