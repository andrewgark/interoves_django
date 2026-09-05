import json
import logging

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from games.telegram.admin_commands import handle_admin_command
from games.telegram.api import send_message
from games.telegram.callbacks import handle_callback_query
from games.telegram.config import is_admin_chat
from games.telegram.public_commands import handle_public_command, parse_public_command

logger = logging.getLogger('application')


def _extract_message_text(message: dict) -> str:
    return (message.get('text') or message.get('caption') or '').strip()


def _handle_account_link_start(message: dict, text: str) -> bool:
    """Consume /start <one-time-token> only in the sender's private bot chat."""
    command, _, token = text.partition(' ')
    if command.lower().split('@')[0] != '/start' or not token.strip():
        return False
    chat = message.get('chat') or {}
    sender = message.get('from') or {}
    chat_id = chat.get('id')
    telegram_user_id = sender.get('id')
    if chat.get('type') != 'private' or str(chat_id) != str(telegram_user_id):
        send_message(chat_id, 'Привязать аккаунт можно только в личном чате с ботом.')
        return True

    from games.telegram_linking import TelegramLinkError, consume_link_token

    try:
        result = consume_link_token(
            token.strip(),
            telegram_user_id=telegram_user_id,
            telegram_username=sender.get('username') or '',
        )
    except TelegramLinkError as exc:
        send_message(chat_id, exc.message)
        return True
    from django.conf import settings

    next_path = result.next_path or '/pay/?telegram=linked'
    if not next_path.startswith('/') or next_path.startswith('//'):
        next_path = '/pay/?telegram=linked'
    return_url = '{}{}'.format(
        (getattr(settings, 'SITE_BASE_URL', '') or 'https://interoves.com').rstrip('/'),
        next_path,
    )
    send_message(
        chat_id,
        'Telegram успешно привязан к аккаунту Inter Oves. '
        '<a href="{}">Вернуться на сайт</a>.'.format(return_url),
    )
    return True


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

    if not text.startswith('/'):
        return

    if _handle_account_link_start(message, text):
        return

    public_command = parse_public_command(text)
    if public_command:
        handle_public_command(public_command, chat_id)
        return

    if not is_admin_chat(chat_id):
        send_message(chat_id, 'Этот бот принимает команды только в admin-чате.')
        return

    reply = handle_admin_command(text)
    if reply:
        send_message(chat_id, reply)
