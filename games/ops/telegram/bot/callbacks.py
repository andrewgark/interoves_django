import logging

from django.db import transaction

from games.club.billing.tickets import accept_ticket_request, reject_ticket_request
from games.models import AlphabettyDictSuggestion, BugReport, TicketRequest
from games.ops.telegram.bot.notify import resolved_keyboard
from games.ops.telegram.transport.api import answer_callback_query, edit_message_reply_markup
from games.puzzles.alphabetty.suggestions import (
    approve_suggestions,
    approve_suggestions_for_answer,
    reject_suggestions,
)

logger = logging.getLogger('application')


def handle_callback_query(callback_query: dict) -> None:
    callback_id = callback_query.get('id')
    data = (callback_query.get('data') or '').strip()
    message = callback_query.get('message') or {}
    chat_id = (message.get('chat') or {}).get('id')
    message_id = message.get('message_id')

    if not data or ':' not in data:
        answer_callback_query(callback_id, 'Неизвестное действие')
        return

    parts = data.split(':')
    if len(parts) != 3:
        answer_callback_query(callback_id, 'Неверные данные кнопки')
        return

    domain, action, raw_id = parts
    try:
        obj_id = int(raw_id)
    except ValueError:
        answer_callback_query(callback_id, 'Неверный id')
        return
    if action == 'done':
        answer_callback_query(callback_id, 'Уже обработано')
        return

    if domain == 'bug':
        _handle_bug(action, obj_id, callback_id, chat_id, message_id)
    elif domain == 'censorly':
        _handle_censorly(action, obj_id, callback_id, chat_id, message_id)
    elif domain == 'ticket':
        _handle_ticket(action, obj_id, callback_id, chat_id, message_id)
    elif domain == 'abdict':
        _handle_abdict(action, obj_id, callback_id, chat_id, message_id)
    elif domain == 'offer':
        from games.ops.telegram.bot.offers import handle_offer_callback

        handle_offer_callback(action, obj_id, callback_id, chat_id, message_id)
    else:
        answer_callback_query(callback_id, 'Неизвестная кнопка')


BUG_STATUS_ACTIONS = {
    'reviewed': (BugReport.STATUS_REVIEWED, '👀 Просмотрено'),
    'fixed': (BugReport.STATUS_FIXED, '✅ Исправлено'),
    'dismiss': (BugReport.STATUS_DISMISSED, '✖️ Отклонено'),
}


def _show_decision(chat_id, message_id, label: str, domain: str, obj_id: int) -> None:
    if chat_id and message_id:
        edit_message_reply_markup(chat_id, message_id, reply_markup=resolved_keyboard(label, domain, obj_id))


def _handle_bug(action: str, report_id: int, callback_id, chat_id, message_id) -> None:
    report = BugReport.objects.filter(pk=report_id).first()
    if report is None:
        answer_callback_query(callback_id, 'Репорт не найден', show_alert=True)
        return
    if action not in BUG_STATUS_ACTIONS:
        answer_callback_query(callback_id, 'Неизвестное действие')
        return
    status, label = BUG_STATUS_ACTIONS[action]
    report.status = status
    report.save(update_fields=['status'])
    answer_callback_query(callback_id, 'Баг #{}: {}'.format(report_id, label))
    _show_decision(chat_id, message_id, label, 'bug', report_id)


def _handle_censorly(action: str, report_id: int, callback_id, chat_id, message_id) -> None:
    report = BugReport.objects.filter(
        pk=report_id, report_type=BugReport.REPORT_TYPE_CENSORLY_WORD_MATCH,
    ).first()
    if report is None:
        answer_callback_query(callback_id, 'Жалоба не найдена', show_alert=True)
        return
    if action not in {'confirm', 'reject', 'review'}:
        answer_callback_query(callback_id, 'Неизвестное действие')
        return

    metadata = dict(report.metadata or {})
    if action == 'confirm':
        expected = metadata.get('expected') or ''
        other = metadata.get('other_word') or ''
        target = metadata.get('target_surface') or ''
        if expected and other and target and not metadata.get('review_case_created'):
            try:
                from games.puzzles.censorly.offline.tools.review_pair import record_review_case

                _row, _created, _diagnostic, review_case = record_review_case(
                    other,
                    target,
                    expected,
                    source='USER_REPORT #{} article={}'.format(report.pk, metadata.get('article') or '—'),
                    note=metadata.get('comment') or '',
                )
            except Exception:
                logger.exception('Failed to create Censorly review case report_id=%s', report.pk)
                answer_callback_query(callback_id, 'Не удалось добавить в очередь разбора', show_alert=True)
                return
            metadata['review_case_created'] = True
            metadata['review_queue_status'] = 'DIAGNOSED'
            report.censorly_review_case = review_case
        metadata['admin_decision'] = 'CONFIRMED'
        report.status = BugReport.STATUS_REVIEWED
        answer = '✅ Подтверждено, в очереди разбора' if expected else '✅ Подтверждено'
    elif action == 'reject':
        metadata['admin_decision'] = 'REJECTED'
        report.status = BugReport.STATUS_DISMISSED
        answer = '✖️ Отклонено'
    else:
        metadata['admin_decision'] = 'NEEDS_REVIEW'
        report.status = BugReport.STATUS_REVIEWED
        answer = '🤔 Нужна проверка'
    report.metadata = metadata
    update_fields = ['status', 'metadata']
    if report.censorly_review_case_id:
        update_fields.append('censorly_review_case')
    report.save(update_fields=update_fields)
    answer_callback_query(callback_id, answer)
    _show_decision(chat_id, message_id, answer, 'censorly', report_id)


def _handle_abdict(action: str, suggestion_id: int, callback_id, chat_id, message_id) -> None:
    qs = AlphabettyDictSuggestion.objects.filter(pk=suggestion_id)
    suggestion = qs.first()
    if suggestion is None:
        answer_callback_query(callback_id, 'Предложение не найдено', show_alert=True)
        return

    word = suggestion.word
    if action == 'approve':
        label = '✅ В словаре'
        if suggestion.status not in (
            AlphabettyDictSuggestion.STATUS_APPROVED,
            AlphabettyDictSuggestion.STATUS_APPROVED_ANSWER,
        ):
            approve_suggestions(qs)
    elif action == 'answer':
        label = '🎯 Для загадывания'
        if suggestion.status != AlphabettyDictSuggestion.STATUS_APPROVED_ANSWER:
            approve_suggestions_for_answer(qs)
    elif action == 'reject':
        label = '✖️ Отклонено'
        if suggestion.status != AlphabettyDictSuggestion.STATUS_REJECTED:
            reject_suggestions(qs)
    else:
        answer_callback_query(callback_id, 'Неизвестное действие')
        return
    answer_callback_query(callback_id, '{}: {}'.format(word, label))
    _show_decision(chat_id, message_id, label, 'abdict', suggestion_id)


def _handle_ticket(action: str, ticket_id: int, callback_id, chat_id, message_id) -> None:
    ticket = TicketRequest.objects.select_related('team').filter(pk=ticket_id).first()
    if ticket is None:
        answer_callback_query(callback_id, 'Заявка не найдена', show_alert=True)
        return

    if action == 'accept':
        with transaction.atomic():
            locked = TicketRequest.objects.select_for_update().select_related('team').get(pk=ticket_id)
            result = accept_ticket_request(locked, source='telegram')
        if result.credited:
            answer_callback_query(callback_id, 'Принято, билеты начислены')
        elif result.already_accepted:
            answer_callback_query(callback_id, 'Уже принята')
        else:
            answer_callback_query(callback_id, 'Принято')
        _show_decision(chat_id, message_id, '✅ Принята', 'ticket', ticket_id)
    elif action == 'reject':
        with transaction.atomic():
            locked = TicketRequest.objects.select_for_update().get(pk=ticket_id)
            result = reject_ticket_request(locked, source='telegram')
        if result.changed:
            answer_callback_query(callback_id, 'Отклонено')
            _show_decision(chat_id, message_id, '✖️ Отклонена', 'ticket', ticket_id)
        else:
            answer_callback_query(callback_id, 'Заявка уже обработана')
            _show_decision(chat_id, message_id, 'Уже обработана', 'ticket', ticket_id)
    else:
        answer_callback_query(callback_id, 'Неизвестное действие')
        return
    return result
