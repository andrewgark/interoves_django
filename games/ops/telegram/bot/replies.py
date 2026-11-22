"""Admin replies to bug reports straight from the admin chat.

Replying (Telegram "Reply") to the bot's message about a bug report adds the
text to the report's thread as an admin reply; the player then gets it on the
site and, through the notifications service, in Telegram. The report is found
by the "#<id>" in the first line of the bot's message, which the bot itself
wrote, so a player's text inside the message cannot point the reply elsewhere.
"""

from __future__ import annotations

import html
import logging

from games.ops.telegram.transport.api import send_message

logger = logging.getLogger('application')


def _admin_user_for(telegram_user_id):
    if not telegram_user_id:
        return None
    from django.contrib.auth.models import User

    return User.objects.filter(profile__telegram_user_id=telegram_user_id).first()


def handle_admin_reply(message: dict) -> bool:
    """Consume a reply to a bug-report message; False when it is not one."""
    reply_to = message.get('reply_to_message') or {}
    if not reply_to or not (reply_to.get('from') or {}).get('is_bot'):
        return False
    text = (message.get('text') or '').strip()
    if not text or text.startswith('/'):
        return False

    original = reply_to.get('text') or reply_to.get('caption') or ''
    from games.ops.telegram.bot.offers import handle_revision_reply

    if handle_revision_reply(message, original):
        return True

    from games.ops.telegram.bot.notify import bug_report_id_from_message_text

    report_id = bug_report_id_from_message_text(original)
    if report_id is None:
        return False

    from games.models import BugReport
    from games.site.pages.profile.feedback import add_admin_reply

    chat_id = (message.get('chat') or {}).get('id')
    report = BugReport.objects.select_related('user').filter(pk=report_id).first()
    if report is None:
        send_message(chat_id, 'Баг #{} не найден.'.format(report_id))
        return True
    if not report.user_id:
        send_message(chat_id, 'Баг #{}: автор не вошёл в аккаунт, ответ ему не дойдёт.'.format(report_id))
        return True
    sender = message.get('from') or {}
    try:
        add_admin_reply(report, _admin_user_for(sender.get('id')), text)
    except ValueError as exc:
        send_message(chat_id, 'Баг #{}: ответ не сохранён — {}'.format(report_id, html.escape(str(exc), quote=False)))
        return True
    except Exception:
        logger.exception('telegram admin reply failed report=%s', report_id)
        send_message(chat_id, 'Баг #{}: не удалось сохранить ответ, попробуйте в /support.'.format(report_id))
        return True
    send_message(
        chat_id, '✉️ Ответ по багу #{} отправлен автору.'.format(report_id),
        reply_to_message_id=message.get('message_id'),
    )
    return True
