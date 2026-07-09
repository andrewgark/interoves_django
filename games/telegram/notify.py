import html
import logging
from typing import Iterable

from django.conf import settings

from games.models import BugReport, CorporateGameOrder, TicketRequest
from games.telegram.api import send_message
from games.telegram.config import (
    admin_chat_id,
    admin_is_muted,
    announce_chat_ids,
    telegram_admin_configured,
    telegram_bot_configured,
)
from games.telegram.game_urls import admin_url, game_site_url

logger = logging.getLogger('application')

CONTACT_METHOD_LABELS = dict(CorporateGameOrder.ContactMethod.choices)

REGISTRATION_MILESTONES = (10, 25, 50, 100, 150, 200)


def telegram_notify_configured() -> bool:
    return telegram_admin_configured()


def _escape(text) -> str:
    if text is None:
        return ''
    return html.escape(str(text), quote=False)


def _join_lines(lines: Iterable[str]) -> str:
    return '\n'.join(line for line in lines if line is not None)


def send_admin_message(text: str, *, reply_markup: dict | None = None, force: bool = False) -> bool:
    if not telegram_admin_configured():
        logger.debug('Telegram admin notify skipped: bot token or admin chat id is empty')
        return False
    if not force and admin_is_muted():
        logger.debug('Telegram admin notify skipped: muted')
        return False
    return send_message(admin_chat_id(), text, reply_markup=reply_markup)


def send_announce_message(text: str, *, reply_markup: dict | None = None) -> bool:
    if not telegram_bot_configured():
        return False
    chat_ids = announce_chat_ids()
    if not chat_ids:
        logger.debug('Telegram announce skipped: TELEGRAM_ANNOUNCE_CHAT_IDS is empty')
        return False
    ok = False
    for chat_id in chat_ids:
        if send_message(chat_id, text, reply_markup=reply_markup):
            ok = True
    return ok


def send_telegram_message(text: str) -> bool:
    """Backward-compatible alias for admin notifications."""
    return send_admin_message(text)


def _admin_link(path: str) -> str:
    return admin_url(path)


def _contact_method_label(order: CorporateGameOrder) -> str:
    if order.contact_method == CorporateGameOrder.ContactMethod.OTHER and order.contact_other_label:
        return 'Другое ({})'.format(order.contact_other_label)
    return CONTACT_METHOD_LABELS.get(order.contact_method, order.contact_method)


def _bug_report_reporter(report: BugReport) -> str:
    if report.team_id:
        team = report.team
        label = getattr(team, 'visible_name', None) or getattr(team, 'name', None) or str(team.pk)
        return 'команда {}'.format(label)
    if report.user_id:
        user = report.user
        if user.get_full_name():
            return user.get_full_name()
        if user.email:
            return user.email
        return user.username or 'user #{}'.format(user.pk)
    if report.anon_key:
        return 'аноним {}'.format(report.anon_key[:8])
    return 'неизвестно'


def bug_report_keyboard(report_id: int) -> dict:
    return {
        'inline_keyboard': [[
            {'text': 'Reviewed', 'callback_data': 'bug:reviewed:{}'.format(report_id)},
            {'text': 'Dismiss', 'callback_data': 'bug:dismiss:{}'.format(report_id)},
        ]],
    }


def ticket_request_keyboard(ticket_id: int) -> dict:
    return {
        'inline_keyboard': [[
            {'text': 'Accept', 'callback_data': 'ticket:accept:{}'.format(ticket_id)},
            {'text': 'Reject', 'callback_data': 'ticket:reject:{}'.format(ticket_id)},
        ]],
    }


def format_bug_report_message(report: BugReport) -> str:
    task = report.task
    game = report.game
    task_label = getattr(task, 'number', None) or task.pk
    game_label = getattr(game, 'name', None) or game.pk
    admin_link = _admin_link('/admin/games/bugreport/{}/change/'.format(report.pk))
    queue_link = _admin_link('/admin/games/pendingbugreport/')
    return _join_lines([
        '🐞 <b>Новый репорт о баге</b>',
        '',
        'Игра: {}'.format(_escape(game_label)),
        'Задание: #{}'.format(_escape(task_label)),
        'Автор: {}'.format(_escape(_bug_report_reporter(report))),
        '',
        _escape(report.text[:3500]),
        '',
        '<a href="{}">Админка</a> · <a href="{}">Очередь</a>'.format(admin_link, queue_link),
    ])


def format_ticket_request_message(ticket_request: TicketRequest) -> str:
    team = ticket_request.team
    team_label = '—'
    if team is not None:
        team_label = getattr(team, 'visible_name', None) or getattr(team, 'name', None) or str(team.pk)
    admin_link = _admin_link('/admin/games/ticketrequest/{}/change/'.format(ticket_request.pk))
    queue_link = _admin_link('/admin/games/pendingticketrequest/')
    return _join_lines([
        '🎫 <b>Новая заявка на билеты</b>',
        '',
        'Команда: {}'.format(_escape(team_label)),
        'Билетов: {}'.format(ticket_request.tickets),
        'Сумма: {} ₽'.format(ticket_request.money),
        'Статус: {}'.format(_escape(ticket_request.status)),
        '',
        '<a href="{}">Админка</a> · <a href="{}">Очередь</a>'.format(admin_link, queue_link),
    ])


def format_corporate_order_message(order: CorporateGameOrder) -> str:
    admin_link = _admin_link('/admin/games/corporategameorder/{}/change/'.format(order.pk))
    queue_link = _admin_link('/admin/games/corporategameorder/')
    lines = [
        '🏢 <b>Новая заявка на корпоративную игру</b>',
        '',
        'Компания: {}'.format(_escape(order.company_name)),
        'Контактное лицо: {}'.format(_escape(order.contact_name)),
        'Способ связи: {}'.format(_escape(_contact_method_label(order))),
        'Контактные данные: {}'.format(_escape(order.contact_value)),
    ]
    if order.team_size:
        lines.append('Размер команды: {}'.format(_escape(order.team_size)))
    if order.preferred_date:
        lines.append('Когда: {}'.format(_escape(order.preferred_date)))
    if order.message:
        lines.extend(['', _escape(order.message[:3500])])
    lines.extend([
        '',
        '<a href="{}">Админка</a> · <a href="{}">Все заявки</a>'.format(admin_link, queue_link),
    ])
    return _join_lines(lines)


def format_payment_message(ticket_request: TicketRequest, event: str) -> str:
    team = ticket_request.team
    team_label = '—'
    if team is not None:
        team_label = getattr(team, 'visible_name', None) or getattr(team, 'name', None) or str(team.pk)
    if event == 'payment.succeeded':
        title = '✅ <b>Оплата билетов прошла</b>'
    else:
        title = '❌ <b>Оплата билетов отменена</b>'
    admin_link = _admin_link('/admin/games/ticketrequest/{}/change/'.format(ticket_request.pk))
    return _join_lines([
        title,
        '',
        'Команда: {}'.format(_escape(team_label)),
        'Билетов: {}'.format(ticket_request.tickets),
        'Сумма: {} ₽'.format(ticket_request.money),
        'Статус: {}'.format(_escape(ticket_request.status)),
        '',
        '<a href="{}">Открыть в админке</a>'.format(admin_link),
    ])


def format_admin_game_lifecycle_message(game, event: str) -> str:
    from django.utils import timezone

    name = _escape(game.get_no_html_name())
    start = timezone.localtime(game.get_visible_start_time()).strftime('%d.%m.%Y %H:%M')
    end = timezone.localtime(game.get_visible_end_time()).strftime('%d.%m.%Y %H:%M')
    site = game_site_url(game)
    admin_game = _admin_link('/admin/games/game/{}/change/'.format(game.id))

    if event == 'start_soon':
        title = '⏰ <b>Через час начинается игра</b>'
    elif event == 'started':
        title = '🟢 <b>Игра началась</b>'
    elif event == 'ended':
        title = '🔴 <b>Игра завершилась</b>'
    else:
        title = 'ℹ️ <b>Игра: {}</b>'.format(_escape(event))

    return _join_lines([
        title,
        '',
        '«{}»'.format(name),
        'Старт: {}'.format(start),
        'Конец: {}'.format(end),
        '',
        '<a href="{}">Сайт</a> · <a href="{}">Админка</a>'.format(_escape(site), admin_game),
    ])


def format_admin_registration_milestone_message(game, count: int) -> str:
    return _join_lines([
        '📈 <b>Регистрации на игру</b>',
        '',
        '«{}»: <b>{}</b> команд'.format(_escape(game.get_no_html_name()), count),
        '<a href="{}">Админка</a>'.format(_admin_link('/admin/games/game/{}/change/'.format(game.id))),
    ])


def notify_new_bug_report(report: BugReport) -> bool:
    return send_admin_message(
        format_bug_report_message(report),
        reply_markup=bug_report_keyboard(report.pk),
    )


def notify_new_ticket_request(ticket_request: TicketRequest) -> bool:
    return send_admin_message(
        format_ticket_request_message(ticket_request),
        reply_markup=ticket_request_keyboard(ticket_request.pk),
    )


def notify_new_corporate_order(order: CorporateGameOrder) -> bool:
    return send_admin_message(format_corporate_order_message(order))


def notify_payment_event(ticket_request: TicketRequest, event: str) -> bool:
    return send_admin_message(format_payment_message(ticket_request, event))


def fetch_recent_telegram_chat_ids() -> list[dict]:
    import requests

    if not settings.TELEGRAM_BOT_TOKEN:
        raise ValueError('TELEGRAM_BOT_TOKEN is not configured')

    url = 'https://api.telegram.org/bot{}/getUpdates'.format(settings.TELEGRAM_BOT_TOKEN)
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    body = response.json()
    if not body.get('ok'):
        raise RuntimeError('Telegram API error: {!r}'.format(body))

    seen = {}
    for update in body.get('result', []):
        message = update.get('message') or update.get('edited_message')
        if not message:
            continue
        chat = message.get('chat') or {}
        chat_id = chat.get('id')
        if chat_id is None:
            continue
        seen[str(chat_id)] = {
            'chat_id': chat_id,
            'type': chat.get('type'),
            'title': chat.get('title'),
            'username': chat.get('username'),
            'first_name': chat.get('first_name'),
            'last_name': chat.get('last_name'),
        }
    return list(seen.values())
