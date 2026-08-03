import html

from django.db import transaction

from games.alphabetty.suggestions import (
    approve_suggestions,
    approve_suggestions_for_answer,
    reject_suggestions,
)
from games.models import AlphabettyDictSuggestion, BugReport, TicketRequest
from games.telegram.api import answer_callback_query, edit_message_reply_markup
from games.telegram.notify import send_admin_message
from games.ticket_service import accept_ticket_request, reject_ticket_request


def handle_callback_query(callback_query: dict) -> None:
    callback_id = callback_query.get('id')
    data = (callback_query.get('data') or '').strip()
    message = callback_query.get('message') or {}
    chat_id = (message.get('chat') or {}).get('id')
    message_id = message.get('message_id')

    if not data or ':' not in data:
        answer_callback_query(callback_id, 'Unknown action')
        return

    parts = data.split(':')
    if len(parts) != 3:
        answer_callback_query(callback_id, 'Bad callback data')
        return

    domain, action, raw_id = parts
    try:
        obj_id = int(raw_id)
    except ValueError:
        answer_callback_query(callback_id, 'Bad id')
        return

    if domain == 'bug':
        _handle_bug(action, obj_id, callback_id, chat_id, message_id)
    elif domain == 'ticket':
        _handle_ticket(action, obj_id, callback_id, chat_id, message_id)
    elif domain == 'abdict':
        _handle_abdict(action, obj_id, callback_id, chat_id, message_id)
    else:
        answer_callback_query(callback_id, 'Unknown domain')


def _handle_bug(action: str, report_id: int, callback_id, chat_id, message_id) -> None:
    report = BugReport.objects.filter(pk=report_id).first()
    if report is None:
        answer_callback_query(callback_id, 'Bug report not found', show_alert=True)
        return

    if action == 'reviewed':
        report.status = 'Reviewed'
        report.save(update_fields=['status'])
        answer_callback_query(callback_id, 'Marked Reviewed')
        send_admin_message('Bug #{} → Reviewed'.format(report_id), force=True)
    elif action == 'dismiss':
        report.status = 'Dismissed'
        report.save(update_fields=['status'])
        answer_callback_query(callback_id, 'Dismissed')
        send_admin_message('Bug #{} → Dismissed'.format(report_id), force=True)
    else:
        answer_callback_query(callback_id, 'Unknown action')
        return

    if chat_id and message_id:
        edit_message_reply_markup(chat_id, message_id, reply_markup=None)


def _handle_abdict(action: str, suggestion_id: int, callback_id, chat_id, message_id) -> None:
    qs = AlphabettyDictSuggestion.objects.filter(pk=suggestion_id)
    suggestion = qs.first()
    if suggestion is None:
        answer_callback_query(callback_id, 'Suggestion not found', show_alert=True)
        return

    word = suggestion.word
    word_html = html.escape(word, quote=False)
    if action == 'approve':
        if suggestion.status in AlphabettyDictSuggestion.STATUSES_VALID:
            answer_callback_query(callback_id, 'Already approved')
        else:
            approve_suggestions(qs)
            answer_callback_query(callback_id, 'Approved: {}'.format(word))
            send_admin_message(
                'Алфавитка словарь: <code>{}</code> → одобрено'.format(word_html),
                force=True,
            )
    elif action == 'answer':
        if suggestion.status == AlphabettyDictSuggestion.STATUS_APPROVED_ANSWER:
            answer_callback_query(callback_id, 'Already in answer pool')
        else:
            approve_suggestions_for_answer(qs)
            answer_callback_query(callback_id, 'For answers: {}'.format(word))
            send_admin_message(
                'Алфавитка словарь: <code>{}</code> → для загадывания'.format(word_html),
                force=True,
            )
    elif action == 'reject':
        if suggestion.status == AlphabettyDictSuggestion.STATUS_REJECTED:
            answer_callback_query(callback_id, 'Already rejected')
        else:
            reject_suggestions(qs)
            answer_callback_query(callback_id, 'Rejected: {}'.format(word))
            send_admin_message(
                'Алфавитка словарь: <code>{}</code> → отклонено'.format(word_html),
                force=True,
            )
    else:
        answer_callback_query(callback_id, 'Unknown action')
        return

    if chat_id and message_id:
        edit_message_reply_markup(chat_id, message_id, reply_markup=None)


def _handle_ticket(action: str, ticket_id: int, callback_id, chat_id, message_id) -> None:
    ticket = TicketRequest.objects.select_related('team').filter(pk=ticket_id).first()
    if ticket is None:
        answer_callback_query(callback_id, 'Ticket not found', show_alert=True)
        return

    if action == 'accept':
        with transaction.atomic():
            locked = TicketRequest.objects.select_for_update().select_related('team').get(pk=ticket_id)
            result = accept_ticket_request(locked, source='telegram')
        if result.credited:
            answer_callback_query(callback_id, 'Accepted, tickets credited')
        elif result.already_accepted:
            answer_callback_query(callback_id, 'Already accepted')
        else:
            answer_callback_query(callback_id, 'Accepted')
        send_admin_message('Ticket #{} → Accepted'.format(ticket_id), force=True)
    elif action == 'reject':
        with transaction.atomic():
            locked = TicketRequest.objects.select_for_update().get(pk=ticket_id)
            result = reject_ticket_request(locked, source='telegram')
        if result.changed:
            answer_callback_query(callback_id, 'Rejected')
            send_admin_message('Ticket #{} → Rejected'.format(ticket_id), force=True)
        else:
            answer_callback_query(callback_id, 'Already finalized')
    else:
        answer_callback_query(callback_id, 'Unknown action')
        return

    if chat_id and message_id:
        edit_message_reply_markup(chat_id, message_id, reply_markup=None)
