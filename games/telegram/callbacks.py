from games.models import BugReport, TicketRequest
from games.telegram.api import answer_callback_query, edit_message_reply_markup
from games.telegram.notify import send_admin_message


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


def _handle_ticket(action: str, ticket_id: int, callback_id, chat_id, message_id) -> None:
    ticket = TicketRequest.objects.select_related('team').filter(pk=ticket_id).first()
    if ticket is None:
        answer_callback_query(callback_id, 'Ticket not found', show_alert=True)
        return

    if action == 'accept':
        if ticket.status != 'Accepted':
            ticket.status = 'Accepted'
            ticket.save(update_fields=['status'])
        answer_callback_query(callback_id, 'Accepted')
        send_admin_message('Ticket #{} → Accepted'.format(ticket_id), force=True)
    elif action == 'reject':
        if ticket.status != 'Rejected':
            ticket.status = 'Rejected'
            ticket.save(update_fields=['status'])
        answer_callback_query(callback_id, 'Rejected')
        send_admin_message('Ticket #{} → Rejected'.format(ticket_id), force=True)
    else:
        answer_callback_query(callback_id, 'Unknown action')
        return

    if chat_id and message_id:
        edit_message_reply_markup(chat_id, message_id, reply_markup=None)
