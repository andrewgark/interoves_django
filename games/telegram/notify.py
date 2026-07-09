import html
import logging
from typing import Iterable
from urllib.parse import urljoin

import requests
from django.conf import settings

from games.models import BugReport, CorporateGameOrder, TicketRequest

logger = logging.getLogger('application')

TELEGRAM_API_TIMEOUT = 10

CONTACT_METHOD_LABELS = dict(CorporateGameOrder.ContactMethod.choices)


def _contact_method_label(order: CorporateGameOrder) -> str:
    if order.contact_method == CorporateGameOrder.ContactMethod.OTHER and order.contact_other_label:
        return 'Другое ({})'.format(order.contact_other_label)
    return CONTACT_METHOD_LABELS.get(order.contact_method, order.contact_method)


def telegram_notify_configured() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_NOTIFY_CHAT_ID)


def _site_base_url() -> str:
    return getattr(settings, 'SITE_BASE_URL', 'https://interoves.com').rstrip('/')


def _admin_url(path: str) -> str:
    return urljoin(_site_base_url() + '/', path.lstrip('/'))


def _escape(text) -> str:
    if text is None:
        return ''
    return html.escape(str(text), quote=False)


def _join_lines(lines: Iterable[str]) -> str:
    return '\n'.join(line for line in lines if line is not None)


def send_telegram_message(text: str) -> bool:
    if not telegram_notify_configured():
        logger.debug('Telegram notify skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_NOTIFY_CHAT_ID is empty')
        return False

    url = 'https://api.telegram.org/bot{}/sendMessage'.format(settings.TELEGRAM_BOT_TOKEN)
    payload = {
        'chat_id': settings.TELEGRAM_NOTIFY_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    try:
        response = requests.post(url, json=payload, timeout=TELEGRAM_API_TIMEOUT)
        response.raise_for_status()
        body = response.json()
        if not body.get('ok'):
            logger.error('Telegram API error: %s', body)
            return False
        return True
    except Exception:
        logger.exception('Failed to send Telegram notification')
        return False


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


def format_bug_report_message(report: BugReport) -> str:
    task = report.task
    game = report.game
    task_label = getattr(task, 'number', None) or task.pk
    game_label = getattr(game, 'name', None) or game.pk
    text_excerpt = _escape(report.text.replace('\n', ' ')[:500])
    if len(report.text) > 500:
        text_excerpt += '…'
    admin_link = _admin_url('/admin/games/bugreport/{}/change/'.format(report.pk))
    queue_link = _admin_url('/admin/games/pendingbugreport/')
    return _join_lines([
        '🐞 <b>Новый репорт о баге</b>',
        '',
        'Игра: {}'.format(_escape(game_label)),
        'Задание: #{}'.format(_escape(task_label)),
        'Автор: {}'.format(_escape(_bug_report_reporter(report))),
        '',
        _escape(report.text[:3500]),
        '',
        '<a href="{}">Открыть в админке</a> · <a href="{}">Очередь</a>'.format(admin_link, queue_link),
    ])


def format_ticket_request_message(ticket_request: TicketRequest) -> str:
    team = ticket_request.team
    team_label = '—'
    if team is not None:
        team_label = getattr(team, 'visible_name', None) or getattr(team, 'name', None) or str(team.pk)
    admin_link = _admin_url('/admin/games/ticketrequest/{}/change/'.format(ticket_request.pk))
    queue_link = _admin_url('/admin/games/pendingticketrequest/')
    return _join_lines([
        '🎫 <b>Новая заявка на билеты</b>',
        '',
        'Команда: {}'.format(_escape(team_label)),
        'Билетов: {}'.format(ticket_request.tickets),
        'Сумма: {} ₽'.format(ticket_request.money),
        'Статус: {}'.format(_escape(ticket_request.status)),
        '',
        '<a href="{}">Открыть в админке</a> · <a href="{}">Очередь</a>'.format(admin_link, queue_link),
    ])


def format_corporate_order_message(order: CorporateGameOrder) -> str:
    admin_link = _admin_url('/admin/games/corporategameorder/{}/change/'.format(order.pk))
    queue_link = _admin_url('/admin/games/corporategameorder/')
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
        '<a href="{}">Открыть в админке</a> · <a href="{}">Все заявки</a>'.format(admin_link, queue_link),
    ])
    return _join_lines(lines)


def notify_new_bug_report(report: BugReport) -> bool:
    return send_telegram_message(format_bug_report_message(report))


def notify_new_ticket_request(ticket_request: TicketRequest) -> bool:
    return send_telegram_message(format_ticket_request_message(ticket_request))


def notify_new_corporate_order(order: CorporateGameOrder) -> bool:
    return send_telegram_message(format_corporate_order_message(order))


def fetch_recent_telegram_chat_ids() -> list[dict]:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise ValueError('TELEGRAM_BOT_TOKEN is not configured')

    url = 'https://api.telegram.org/bot{}/getUpdates'.format(settings.TELEGRAM_BOT_TOKEN)
    response = requests.get(url, timeout=TELEGRAM_API_TIMEOUT)
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
