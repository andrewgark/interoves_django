import html
import logging
from typing import Iterable

from django.conf import settings

from games.models import (
    AlphabettyDictSuggestion,
    BugReport,
    CorporateGameOrder,
    Donation,
    TicketRequest,
)
from games.telegram.api import send_message, send_photo
from games.telegram.config import (
    admin_chat_id,
    admin_is_muted,
    announce_chat_ids,
    telegram_admin_configured,
    telegram_bot_configured,
)
from games.telegram.game_urls import (
    admin_url,
    game_admin_url,
    game_site_url,
    task_admin_url,
    task_group_admin_url,
    task_play_url,
)

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


def send_announce_photo(
    photo_bytes: bytes,
    *,
    caption: str = '',
    filename: str = 'photo.png',
    reply_markup: dict | None = None,
) -> bool:
    if not telegram_bot_configured():
        return False
    chat_ids = announce_chat_ids()
    if not chat_ids:
        logger.debug('Telegram announce photo skipped: TELEGRAM_ANNOUNCE_CHAT_IDS is empty')
        return False
    ok = False
    for chat_id in chat_ids:
        if send_photo(
            chat_id,
            photo_bytes,
            caption=caption,
            filename=filename,
            reply_markup=reply_markup,
        ):
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


def alphabetty_dict_suggestion_keyboard(suggestion_id: int) -> dict:
    return {
        'inline_keyboard': [[
            {'text': 'Одобрить', 'callback_data': 'abdict:approve:{}'.format(suggestion_id)},
            {'text': 'Отклонить', 'callback_data': 'abdict:reject:{}'.format(suggestion_id)},
        ]],
    }


def _dict_suggestion_reporter(suggestion: AlphabettyDictSuggestion) -> str:
    if suggestion.user_id:
        user = suggestion.user
        if user.get_full_name():
            return user.get_full_name()
        if user.email:
            return user.email
        return user.username or 'user #{}'.format(user.pk)
    if suggestion.anon_key:
        return 'аноним {}'.format(suggestion.anon_key[:8])
    return 'неизвестно'


def format_alphabetty_dict_suggestion_message(suggestion: AlphabettyDictSuggestion) -> str:
    admin_link = _admin_link(
        '/admin/games/alphabettydictsuggestion/{}/change/'.format(suggestion.pk)
    )
    queue_link = _admin_link('/admin/games/pendingalphabettydictsuggestion/')
    return _join_lines([
        '🔤 <b>Предложение в словарь Алфавитки</b>',
        '',
        'Слово: <code>{}</code>'.format(_escape(suggestion.word)),
        'Голосов: {}'.format(suggestion.suggest_count),
        'Автор: {}'.format(_escape(_dict_suggestion_reporter(suggestion))),
        '',
        '<a href="{}">Запись</a> · <a href="{}">Очередь</a>'.format(admin_link, queue_link),
    ])


def notify_new_alphabetty_dict_suggestion(suggestion: AlphabettyDictSuggestion) -> bool:
    suggestion = (
        AlphabettyDictSuggestion.objects
        .select_related('user')
        .get(pk=suggestion.pk)
    )
    return send_admin_message(
        format_alphabetty_dict_suggestion_message(suggestion),
        reply_markup=alphabetty_dict_suggestion_keyboard(suggestion.pk),
    )


def format_bug_report_message(report: BugReport) -> str:
    task = report.task
    game = report.game
    task_label = getattr(task, 'number', None) or task.pk
    if hasattr(game, 'get_no_html_name'):
        game_label = game.get_no_html_name()
    else:
        game_label = getattr(game, 'name', None) or game.pk
    task_site = task_play_url(game, task)
    game_site = game_site_url(game)
    report_admin = _admin_link('/admin/games/bugreport/{}/change/'.format(report.pk))
    queue_link = _admin_link('/admin/games/pendingbugreport/')
    task_admin = task_admin_url(task)
    game_admin = game_admin_url(game)
    task_group = getattr(task, 'task_group', None)
    task_group_admin = task_group_admin_url(task_group) if task_group is not None else ''

    task_admin_links = ['<a href="{}">task</a>'.format(_escape(task_admin))]
    if task_group_admin:
        task_admin_links.append('<a href="{}">taskgroup</a>'.format(_escape(task_group_admin)))

    return _join_lines([
        '🐞 <b>Новый репорт о баге</b>',
        '',
        'Игра: <a href="{}">{}</a> · <a href="{}">админка</a>'.format(
            _escape(game_site), _escape(game_label), _escape(game_admin),
        ),
        'Задание: <a href="{}">#{}</a> · {}'.format(
            _escape(task_site), _escape(task_label), ' · '.join(task_admin_links),
        ),
        'Автор: {}'.format(_escape(_bug_report_reporter(report))),
        '',
        _escape(report.text[:3500]),
        '',
        '<a href="{}">Репорт</a> · <a href="{}">Очередь</a>'.format(report_admin, queue_link),
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
    report = (
        BugReport.objects
        .select_related('task', 'task__task_group', 'game', 'game__project', 'team', 'user')
        .get(pk=report.pk)
    )
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


def format_donation_message(donation: Donation, event: str = 'created') -> str:
    admin_link = _admin_link('/admin/games/donation/{}/change/'.format(donation.pk))
    if event == 'donation.confirmed':
        title = '✅ <b>Донат подтверждён</b>'
    elif event == 'donation.rejected':
        title = '❌ <b>Донат отклонён</b>'
    else:
        title = '💚 <b>Новый крипто-донат</b>'
    lines = [
        title,
        '',
        'Сумма инвойса: {} ₽'.format(donation.amount_rub),
        'Статус: {}'.format(_escape(donation.status)),
    ]
    if donation.pay_amount and donation.pay_currency:
        lines.append('Оплачено: {} {}'.format(_escape(donation.pay_amount), _escape(donation.pay_currency)))
    if donation.user_id:
        lines.append('User id: {}'.format(donation.user_id))
    lines.extend(['', '<a href="{}">Открыть в админке</a>'.format(admin_link)])
    return _join_lines(lines)


def notify_new_donation(donation: Donation) -> bool:
    return send_admin_message(format_donation_message(donation, 'created'))


def notify_donation_event(donation: Donation, event: str) -> bool:
    return send_admin_message(format_donation_message(donation, event))


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
