import html
from typing import Iterable

from django.utils import timezone

from games.telegram.game_urls import (
    game_answers_url,
    game_conditions_url,
    game_site_url,
    game_standings_url,
)
from games.telegram.models import TelegramGameAnnouncement


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


def format_game_start_announcement(game) -> str:
    name = _escape(game.get_no_html_name())
    lines = [
        '🚀 <b>Игра «{}» началась!</b>'.format(name),
        '',
        'Удачи всем командам!',
    ]
    links = []
    conditions = game_conditions_url(game)
    if conditions:
        links.append(_link('Условия', conditions))
    links.append(_link('Сайт', game_site_url(game)))
    if links:
        lines.extend(['', ' · '.join(links)])
    return _join_lines(lines)


def format_game_end_soon_announcement(game) -> str:
    name = _escape(game.get_no_html_name())
    end = game.get_visible_end_time()
    end_local = timezone.localtime(end).strftime('%H:%M')
    return _join_lines([
        '⏳ <b>До конца игры «{}» — 30 минут</b> (окончание в {})'.format(name, end_local),
        '',
        'Если застряли — самое время брать подсказки на сайте!',
        _link('Открыть игру', game_site_url(game)),
    ])


def format_game_end_announcement(game) -> str:
    name = _escape(game.get_no_html_name())
    lines = [
        '🏁 <b>Игра «{}» завершилась!</b>'.format(name),
        '',
        'Спасибо всем, кто играл!',
    ]
    links = []
    answers = game_answers_url(game)
    if answers:
        links.append(_link('Ответы', answers))
    links.append(_link('Таблица результатов', game_standings_url(game)))
    if links:
        lines.extend(['', ' · '.join(links)])
    return _join_lines(lines)


ANNOUNCEMENT_FORMATTERS = {
    TelegramGameAnnouncement.KIND_START: format_game_start_announcement,
    TelegramGameAnnouncement.KIND_END_SOON_30: format_game_end_soon_announcement,
    TelegramGameAnnouncement.KIND_END: format_game_end_announcement,
}
