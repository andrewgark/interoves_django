"""Public Telegram commands available in any chat (/des, /des_results)."""

from __future__ import annotations

import html
import logging
import os
from datetime import timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from django.utils import formats, timezone

from games.models import Game
from games.telegram.api import send_message, send_photo
from games.telegram.game_urls import game_tournament_results_url

logger = logging.getLogger('application')

MOSCOW = ZoneInfo('Europe/Moscow')
PUBLIC_COMMANDS = frozenset({'/des', '/des_results'})
NO_GAME_REPLY = 'Анонсированной десяточки сейчас нет.'


def _escape(text) -> str:
    if text is None:
        return ''
    return html.escape(str(text), quote=False)


def _join_lines(lines: Iterable[str]) -> str:
    return '\n'.join(line for line in lines if line is not None)


def parse_public_command(text: str) -> str | None:
    text = (text or '').strip()
    if not text.startswith('/'):
        return None
    command = text.split(maxsplit=1)[0].lower().split('@')[0]
    if command in PUBLIC_COMMANDS:
        return command
    return None


def handle_public_command(command: str, chat_id) -> None:
    if command == '/des':
        _reply_des(chat_id)
        return
    if command == '/des_results':
        _reply_des_results(chat_id)
        return


def list_public_desyatochki() -> list[Game]:
    games = []
    for game in Game.objects.filter(project_id='main').select_related('project'):
        if game.has_access('see_game_preview', team=None):
            games.append(game)
    return games


def select_desyatka_for_public(*, now=None) -> Game | None:
    """
    Ближайшая публичная десяточка:
    1) ближайшая будущая (ещё не стартовала);
    2) иначе идущая прямо сейчас;
    3) иначе закончившаяся менее суток назад;
    4) иначе None.
    """
    now = now or timezone.now()
    games = list_public_desyatochki()
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


def format_duration(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if total < 0:
        total = 0
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append('{} {}'.format(days, _ru_plural(days, 'день', 'дня', 'дней')))
    if hours or days:
        parts.append('{} {}'.format(hours, _ru_plural(hours, 'час', 'часа', 'часов')))
    parts.append('{} {}'.format(minutes, _ru_plural(minutes, 'минута', 'минуты', 'минут')))
    return ' '.join(parts)


def format_des_status_line(game: Game, *, now=None) -> str:
    now = now or timezone.now()
    if now < game.start_time:
        return 'До начала: {}'.format(format_duration(game.start_time - now))
    if now <= game.end_time:
        return 'Идёт уже {}, осталось {}'.format(
            format_duration(now - game.start_time),
            format_duration(game.end_time - now),
        )
    return 'Закончилась {} назад'.format(format_duration(now - game.end_time))


def format_des_card_caption(game: Game, *, now=None) -> str:
    now = now or timezone.now()
    start = game.get_visible_start_time().astimezone(MOSCOW)
    end = game.get_visible_end_time().astimezone(MOSCOW)
    title = game.get_no_html_name()
    lines = [
        '<b>{}</b>'.format(_escape(formats.date_format(start, 'd E Y'))),
        '{} – {}'.format(
            _escape(formats.time_format(start, 'H:i')),
            _escape(formats.time_format(end, 'H:i')),
        ),
        '',
        '<b>{}</b>'.format(_escape(title)),
    ]
    if game.theme:
        lines.append('Тема: {}'.format(_escape(game.theme)))
    lines.append('Автор: {}'.format(_escape(game.author)))
    lines.extend(['', format_des_status_line(game, now=now)])
    return _join_lines(lines)


def read_game_announce_image(game: Game) -> tuple[bytes, str] | None:
    if not game.image:
        return None
    try:
        with game.image.open('rb') as fh:
            data = fh.read()
    except Exception:
        logger.exception('Failed to read announce image for game %s', game.id)
        return None
    if not data:
        return None
    name = os.path.basename(getattr(game.image, 'name', '') or '') or 'announce.jpg'
    return data, name


def first_place_teams(game: Game) -> list:
    from games.views.new_ui import _load_game_results_data

    data = _load_game_results_data(game, 'tournament')
    team_to_place = data.get('team_to_place') or {}
    winners = [team for team, place in team_to_place.items() if place == 1]
    winners.sort(key=lambda t: ((getattr(t, 'visible_name', None) or getattr(t, 'name', '') or '').lower(), str(t)))
    return winners


def team_display_name(team) -> str:
    return (getattr(team, 'visible_name', None) or getattr(team, 'name', None) or str(team))


def format_des_results_caption(game: Game, winners: list) -> str:
    title = game.get_no_html_name()
    results_url = game_tournament_results_url(game)
    lines = [
        '<b>{}</b> — турнирные результаты'.format(_escape(title)),
        '',
    ]
    if winners:
        if len(winners) == 1:
            lines.append('1 место: <b>{}</b>'.format(_escape(team_display_name(winners[0]))))
        else:
            lines.append('1 место ({}):'.format(len(winners)))
            for team in winners:
                lines.append('• <b>{}</b>'.format(_escape(team_display_name(team))))
    else:
        lines.append('Пока нет результатов.')
    lines.extend([
        '',
        '<a href="{}">Таблица результатов</a>'.format(_escape(results_url)),
    ])
    return _join_lines(lines)


def _reply_des(chat_id) -> None:
    game = select_desyatka_for_public()
    if game is None:
        send_message(chat_id, NO_GAME_REPLY)
        return

    caption = format_des_card_caption(game)
    image = read_game_announce_image(game)
    if image:
        photo_bytes, filename = image
        if send_photo(chat_id, photo_bytes, caption=caption, filename=filename):
            return
        logger.warning('send_photo failed for /des game=%s; falling back to text', game.id)
    send_message(chat_id, caption)


def _reply_des_results(chat_id) -> None:
    game = select_desyatka_for_public()
    if game is None:
        send_message(chat_id, NO_GAME_REPLY)
        return

    now = timezone.now()
    if now < game.start_time:
        send_message(
            chat_id,
            _join_lines([
                'Турнирные результаты ещё недоступны — игра не началась.',
                format_des_status_line(game, now=now),
            ]),
        )
        return

    if not game.has_access('see_tournament_results', team=None):
        send_message(chat_id, 'Турнирные результаты сейчас недоступны.')
        return

    winners = first_place_teams(game)
    caption = format_des_results_caption(game, winners)

    png = None
    try:
        from games.telegram.results_image import render_tournament_results_png

        png = render_tournament_results_png(game)
    except Exception:
        logger.exception('Tournament results screenshot failed for game %s', game.id)

    if png and send_photo(chat_id, png, caption=caption, filename='results.png'):
        return
    send_message(chat_id, caption)
