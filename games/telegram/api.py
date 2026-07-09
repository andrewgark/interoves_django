import logging
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger('application')

TELEGRAM_API_TIMEOUT = 10


def _api_url(method: str) -> str:
    return 'https://api.telegram.org/bot{}/{}'.format(settings.TELEGRAM_BOT_TOKEN, method)


def _post(method: str, payload: dict) -> dict | None:
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.debug('Telegram API skipped: TELEGRAM_BOT_TOKEN is empty')
        return None
    try:
        response = requests.post(_api_url(method), json=payload, timeout=TELEGRAM_API_TIMEOUT)
        response.raise_for_status()
        body = response.json()
        if not body.get('ok'):
            logger.error('Telegram API %s error: %s', method, body)
            return None
        return body
    except Exception:
        logger.exception('Telegram API %s failed', method)
        return None


def send_message(
    chat_id,
    text: str,
    *,
    parse_mode: str = 'HTML',
    disable_web_page_preview: bool = True,
    reply_markup: dict | None = None,
) -> bool:
    payload: dict[str, Any] = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode,
        'disable_web_page_preview': disable_web_page_preview,
    }
    if reply_markup is not None:
        payload['reply_markup'] = reply_markup
    return _post('sendMessage', payload) is not None


def answer_callback_query(callback_query_id: str, text: str = '', *, show_alert: bool = False) -> bool:
    payload = {
        'callback_query_id': callback_query_id,
        'text': text,
        'show_alert': show_alert,
    }
    return _post('answerCallbackQuery', payload) is not None


def edit_message_reply_markup(chat_id, message_id, reply_markup: dict | None = None) -> bool:
    payload = {
        'chat_id': chat_id,
        'message_id': message_id,
        'reply_markup': reply_markup or {},
    }
    return _post('editMessageReplyMarkup', payload) is not None


def set_webhook(url: str, *, secret_token: str = '') -> bool:
    payload: dict[str, Any] = {'url': url, 'drop_pending_updates': False}
    if secret_token:
        payload['secret_token'] = secret_token
    return _post('setWebhook', payload) is not None


def delete_webhook() -> bool:
    return _post('deleteWebhook', {'drop_pending_updates': False}) is not None


def get_webhook_info() -> dict | None:
    if not settings.TELEGRAM_BOT_TOKEN:
        return None
    try:
        response = requests.get(_api_url('getWebhookInfo'), timeout=TELEGRAM_API_TIMEOUT)
        response.raise_for_status()
        body = response.json()
        if body.get('ok'):
            return body.get('result') or {}
    except Exception:
        logger.exception('Telegram getWebhookInfo failed')
    return None
