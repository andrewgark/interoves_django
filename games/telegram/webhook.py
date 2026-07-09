import json
import logging

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from games.telegram.admin_commands import handle_admin_command
from games.telegram.api import send_message
from games.telegram.callbacks import handle_callback_query
from games.telegram.config import is_admin_chat

logger = logging.getLogger('application')


def _extract_message_text(message: dict) -> str:
    return (message.get('text') or message.get('caption') or '').strip()


@csrf_exempt
def telegram_webhook(request, secret: str = ''):
    from django.conf import settings

    configured_secret = getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', '') or ''
    if configured_secret and secret != configured_secret:
        return HttpResponse(status=403)

    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        update = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponse(status=400)

    try:
        _dispatch_update(update)
    except Exception:
        logger.exception('Telegram webhook handler failed')

    return HttpResponse('ok')


def _dispatch_update(update: dict) -> None:
    if 'callback_query' in update:
        callback = update['callback_query']
        chat_id = ((callback.get('message') or {}).get('chat') or {}).get('id')
        if not is_admin_chat(chat_id):
            from games.telegram.api import answer_callback_query

            answer_callback_query(callback.get('id'), 'Admin only', show_alert=True)
            return
        handle_callback_query(callback)
        return

    message = update.get('message') or update.get('edited_message')
    if not message:
        return

    chat = message.get('chat') or {}
    chat_id = chat.get('id')
    text = _extract_message_text(message)
    if not text:
        return

    if not is_admin_chat(chat_id):
        if text.startswith('/'):
            send_message(chat_id, 'Этот бот принимает команды только в admin-чате.')
        return

    if not text.startswith('/'):
        return

    reply = handle_admin_command(text)
    if reply:
        send_message(chat_id, reply)
