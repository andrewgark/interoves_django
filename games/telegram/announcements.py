"""Formatters for public chat game lifecycle announcements."""

from __future__ import annotations

import html
import random
from collections import defaultdict
from typing import Iterable
from zoneinfo import ZoneInfo

from django.utils import timezone

from games.telegram.game_urls import (
    game_answers_url,
    game_conditions_copy_url,
    game_conditions_url,
    game_site_url,
    game_tournament_results_url,
)
from games.telegram.models import TelegramGameAnnouncement

CONGRATS_EMOJI_POOL = (
    '🎉', '🎊', '🥳', '👏', '🙌', '✨', '🌟', '⭐', '💫', '🔥',
    '🏆', '🥇', '🎖️', '💪', '🚀', '🎯', '💥', '🤩', '😁', '😄',
    '🍾', '🥂', '🎈', '🌈', '☀️', '💖', '💯', '👑', '🐉', '⚡',
    '🧩', '🧠', '🎁', '🌸', '🍀', '🦄', '🐱', '🐶', '🎵', '🎸',
)


def _escape(text) -> str:
    if text is None:
        return ''
    return html.escape(str(text), quote=False)


def _join_lines(lines: Iterable[str]) -> str:
    return '\n'.join(line for line in lines if line is not None)


def _link(label: str, url: str) -> str:
    if not url:
        return ''
    return '<a href="{}">{}</a>'.format(_escape(url), _escape(label))


def team_display_name(team) -> str:
    return (getattr(team, 'visible_name', None) or getattr(team, 'name', None) or str(team))


def _ru_plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    rem = n % 10
    if rem == 1:
        return one
    if 2 <= rem <= 4:
        return few
    return many


def _teams_count_label(n: int) -> str:
    return '{} {}'.format(n, _ru_plural(n, 'команда', 'команды', 'команд'))


def random_congrats_emojis(n: int = 6) -> str:
    n = max(5, min(n, len(CONGRATS_EMOJI_POOL)))
    return ''.join(random.sample(CONGRATS_EMOJI_POOL, n))


def format_game_day_before_announcement(game) -> str:
    name = _escape(game.get_no_html_name())
    start_local = timezone.localtime(game.start_time).strftime('%H:%M')
    return _join_lines([
        '📅 <b>{} уже завтра!</b>'.format(name),
        'Начало в {} по МСК.'.format(start_local),
        '',
        'Не забудьте зарегистрировать команду и купить билеты.',
        _link('Открыть игру', game_site_url(game)),
    ])


def format_game_hour_before_announcement(game) -> str:
    name = _escape(game.get_no_html_name())
    return _join_lines([
        '⏰ <b>Через час начинается {}!</b>'.format(name),
        '',
        _link('Открыть игру', game_site_url(game)),
    ])


def format_game_start_announcement(game) -> str:
    name = _escape(game.get_no_html_name())
    lines = [
        '🚀 <b>Начали!</b>',
        '',
        '{} стартовала. Удачи всем командам!'.format(name),
    ]
    links = [_link('Сайт', game_site_url(game))]
    conditions = game_conditions_url(game)
    if conditions:
        links.append(_link('Условия', conditions))
        copy_conditions = game_conditions_copy_url(game)
        if copy_conditions:
            links.append(_link('Скопировать условия', copy_conditions))
    lines.extend(['', ' · '.join(links)])
    return _join_lines(lines)


def format_game_end_soon_announcement(game) -> str:
    """Legacy 30-minute copy (kept for old tests / rows)."""
    name = _escape(game.get_no_html_name())
    end = game.end_time
    end_local = timezone.localtime(end).strftime('%H:%M')
    return _join_lines([
        '⏳ <b>До конца {} — 30 минут</b> (окончание в {})'.format(name, end_local),
        '',
        'Если застряли — самое время брать подсказки на сайте!',
        _link('Открыть игру', game_site_url(game)),
    ])


def format_game_end_soon_15_announcement(game) -> str:
    name = _escape(game.get_no_html_name())
    end_local = timezone.localtime(game.end_time).strftime('%H:%M')
    return _join_lines([
        '⏳ <b>До конца {} — 15 минут</b> (окончание в {})'.format(name, end_local),
        '',
        'Срочно берите подсказки, если застряли!',
        _link('Открыть игру', game_site_url(game)),
    ])


def format_game_end_announcement(game) -> str:
    name = _escape(game.get_no_html_name())
    lines = [
        '🏁 <b>{} завершилась!</b>'.format(name),
        '',
        'Спасибо всем, кто играл!',
    ]
    answers = game_answers_url(game)
    if answers:
        lines.extend(['', _link('Ответы', answers)])
    return _join_lines(lines)


def _all_solved_game_phrase(game) -> str:
    """Phrase after «в »: prepositional for Десяточка*, else «игре {name}»."""
    raw = game.get_no_html_name() or ''
    prefix = 'Десяточка'
    if raw.startswith(prefix):
        return _escape('Десяточке' + raw[len(prefix):])
    return 'игре {}'.format(_escape(raw))


def _format_points_amount(n) -> str:
    from decimal import Decimal

    d = Decimal(str(n or 0))
    if d == d.to_integral_value():
        return str(int(d))
    # Trim trailing zeros: 1.50 → 1.5
    normalized = d.normalize()
    text = format(normalized, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _points_word(n) -> str:
    from decimal import Decimal

    d = Decimal(str(n or 0))
    if d != d.to_integral_value():
        return 'балла'
    return _ru_plural(int(d), 'балл', 'балла', 'баллов')


def format_hints_taken_line(hint_count: int = 0, hint_penalty=0) -> str:
    """«Команда взяла N подсказку/и/ок и потеряла на этом M балл/а/ов.»"""
    n = int(hint_count or 0)
    return 'Команда взяла {} {} и потеряла на этом {} {}.'.format(
        n,
        _ru_plural(n, 'подсказку', 'подсказки', 'подсказок'),
        _format_points_amount(hint_penalty),
        _points_word(hint_penalty),
    )


def format_all_solved_announcement(game, team, hint_count: int = 0, hint_penalty=0) -> str:
    team_name = _escape(team_display_name(team))
    lines = [
        '🎉 Команда <b>{}</b> решила все задания в {}!'.format(
            team_name, _all_solved_game_phrase(game),
        ),
    ]
    if int(hint_count or 0) > 0:
        lines.extend(['', format_hints_taken_line(hint_count, hint_penalty)])
    lines.extend(['', 'Поздравляем!', random_congrats_emojis(6)])
    return _join_lines(lines)


def format_no_coffins_announcement(task_number, task_name, team, taken_at) -> str:
    task_label = '№{}'.format(_escape(task_number))
    if task_name:
        task_label += ' «{}»'.format(_escape(task_name))
    taken_at_msk = timezone.localtime(taken_at, ZoneInfo('Europe/Moscow')).strftime('%H:%M')
    return _join_lines([
        '🎯 <b>Не осталось заданий-гробов!</b>',
        'Каждое задание взяла хотя бы одна команда.',
        '',
        '🔓 Последнее взятое задание — <b>{}</b>.'.format(task_label),
        '👏 Команда <b>{}</b> взяла его в <b>{}</b> по МСК.'.format(
            _escape(team_display_name(team)), taken_at_msk,
        ),
    ])


def format_game_results_announcement(game, podium: dict[int, list]) -> str:
    """
    podium: {1: [teams...], 2: [...], 3: [...]} using Team objects.
    If ≥3 teams on place 1, only list first place.
    """
    name = _escape(game.get_no_html_name())
    results_url = game_tournament_results_url(game)
    first = podium.get(1) or []

    lines = [
        '🏆 <b>Результаты: {}</b>'.format(name),
        '',
    ]

    if not first:
        lines.append('Пока нет результатов.')
    elif len(first) >= 3:
        lines.append('1 место ({}):'.format(_teams_count_label(len(first))))
        for team in first:
            lines.append('• <b>{}</b>'.format(_escape(team_display_name(team))))
        lines.extend(['', 'Поздравляем всех победителей!'])
    else:
        medals = {1: '🥇', 2: '🥈', 3: '🥉'}
        for place in (1, 2, 3):
            teams = podium.get(place) or []
            if not teams:
                continue
            medal = medals[place]
            if len(teams) == 1:
                lines.append(
                    '{} {} место: <b>{}</b>'.format(
                        medal, place, _escape(team_display_name(teams[0])),
                    )
                )
            else:
                lines.append('{} {} место:'.format(medal, place))
                for team in teams:
                    lines.append('• <b>{}</b>'.format(_escape(team_display_name(team))))
        lines.extend(['', 'Поздравляем!'])

    lines.extend([
        random_congrats_emojis(6),
        '',
        _link('Таблица результатов', results_url),
    ])
    return _join_lines(lines)


def build_podium(team_to_place: dict, teams_order=None) -> dict[int, list]:
    """Build podium while preserving the results table row order within ties."""
    podium: dict[int, list] = defaultdict(list)
    ordered_teams = teams_order if teams_order is not None else (team_to_place or {}).keys()
    for team in ordered_teams:
        place = (team_to_place or {}).get(team)
        if place in (1, 2, 3):
            podium[place].append(team)
    return dict(podium)


ANNOUNCEMENT_FORMATTERS = {
    TelegramGameAnnouncement.KIND_DAY_BEFORE: format_game_day_before_announcement,
    TelegramGameAnnouncement.KIND_HOUR_BEFORE: format_game_hour_before_announcement,
    TelegramGameAnnouncement.KIND_START: format_game_start_announcement,
    TelegramGameAnnouncement.KIND_END_SOON_15: format_game_end_soon_15_announcement,
    TelegramGameAnnouncement.KIND_END_SOON_30: format_game_end_soon_announcement,
    TelegramGameAnnouncement.KIND_END: format_game_end_announcement,
}
