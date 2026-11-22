import logging
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger('application')

TELEGRAM_API_TIMEOUT = 10
TELEGRAM_UPLOAD_TIMEOUT = 60


def _api_url(method: str) -> str:
    return 'https://api.telegram.org/bot{}/{}'.format(settings.TELEGRAM_BOT_TOKEN, method)


def _safe_error_text(error: Exception) -> str:
    token = str(getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or '')
    text = str(error)
    return text.replace(token, '[redacted]') if token else text


def _error_description(response) -> str:
    """Telegram's own reason for a rejected call ("can't parse entities: …")."""
    try:
        return str((response.json() or {}).get('description') or '')[:300]
    except Exception:
        return ''


def _post_detailed(method: str, payload: dict) -> tuple[dict | None, str]:
    """POST a Bot API call: (body, '') on success, (None, Telegram's reason) on failure."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.debug('Telegram API skipped: TELEGRAM_BOT_TOKEN is empty')
        return None, ''
    response = None
    try:
        response = requests.post(_api_url(method), json=payload, timeout=TELEGRAM_API_TIMEOUT)
        response.raise_for_status()
        body = response.json()
        if not body.get('ok'):
            logger.error('Telegram API %s error: %s', method, body)
            return None, str(body.get('description') or '')
        return body, ''
    except Exception as error:
        description = _error_description(response) if response is not None else ''
        # The bot token is part of the request URL and requests includes that
        # URL in connection errors. Never put it into application logs.
        logger.error(
            'Telegram API %s failed: %s%s', method, _safe_error_text(error),
            ' description={!r}'.format(description) if description else '',
        )
        return None, description


def _post(method: str, payload: dict) -> dict | None:
    return _post_detailed(method, payload)[0]


def _plain_text(text: str) -> str:
    import html
    import re

    return html.unescape(re.sub(r'<[^>]+>', '', text or ''))


def _post_multipart(method: str, data: dict, files: dict) -> dict | None:
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.debug('Telegram API skipped: TELEGRAM_BOT_TOKEN is empty')
        return None
    try:
        response = requests.post(
            _api_url(method),
            data=data,
            files=files,
            timeout=TELEGRAM_UPLOAD_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get('ok'):
            logger.error('Telegram API %s error: %s', method, body)
            return None
        return body
    except Exception as error:
        logger.error('Telegram API %s failed: %s', method, _safe_error_text(error))
        return None


def send_message_result(
    chat_id,
    text: str,
    *,
    parse_mode: str = 'HTML',
    disable_web_page_preview: bool = True,
    reply_markup: dict | None = None,
    reply_to_message_id: int | None = None,
) -> dict | None:
    """Send a message; the sent Message object (with ``message_id``) or None."""
    payload: dict[str, Any] = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode,
        'disable_web_page_preview': disable_web_page_preview,
    }
    if reply_markup is not None:
        payload['reply_markup'] = reply_markup
    if reply_to_message_id:
        # The original may have been deleted; the message still goes out, unthreaded.
        payload['reply_parameters'] = {
            'message_id': int(reply_to_message_id), 'allow_sending_without_reply': True,
        }
    body, description = _post_detailed('sendMessage', payload)
    if body is not None:
        return body.get('result') or {}
    reason = description.lower()
    if parse_mode == 'HTML' and ("can't parse entities" in reason or 'message is too long' in reason):
        # Better an unformatted message than a lost one: alerts are not retried.
        logger.warning('Telegram sendMessage resent as plain text: %s', description)
        fallback = dict(payload, text=_plain_text(text)[:4000])
        fallback.pop('parse_mode', None)
        body = _post('sendMessage', fallback)
        return (body.get('result') or {}) if body is not None else None
    return None


def send_message(
    chat_id,
    text: str,
    *,
    parse_mode: str = 'HTML',
    disable_web_page_preview: bool = True,
    reply_markup: dict | None = None,
    reply_to_message_id: int | None = None,
) -> bool:
    return send_message_result(
        chat_id, text, parse_mode=parse_mode,
        disable_web_page_preview=disable_web_page_preview,
        reply_markup=reply_markup, reply_to_message_id=reply_to_message_id,
    ) is not None


SEND_OK = 'ok'
SEND_BLOCKED = 'blocked'
SEND_FAILED = 'failed'


def _post_status(method: str, *, json_payload=None, data=None, files=None) -> tuple[str, dict]:
    """POST and classify: (SEND_OK | SEND_BLOCKED | SEND_FAILED, result)."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.debug('Telegram API skipped: TELEGRAM_BOT_TOKEN is empty')
        return SEND_FAILED, {}
    try:
        response = requests.post(
            _api_url(method), json=json_payload, data=data, files=files,
            timeout=TELEGRAM_UPLOAD_TIMEOUT if files else TELEGRAM_API_TIMEOUT,
        )
    except Exception as error:
        logger.error('Telegram API %s failed: %s', method, _safe_error_text(error))
        return SEND_FAILED, {}
    # 403: the user blocked the bot or never started it.
    if response.status_code == 403:
        return SEND_BLOCKED, {}
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.ok and body.get('ok'):
        return SEND_OK, body.get('result') or {}
    logger.error('Telegram API %s error: status=%s body=%s', method, response.status_code, body)
    return SEND_FAILED, {}


def send_message_status(
    chat_id,
    text: str,
    *,
    parse_mode: str = 'HTML',
    disable_web_page_preview: bool = True,
    reply_markup: dict | None = None,
) -> str:
    """Like ``send_message`` but tells a blocked bot (403) apart from other failures."""
    payload: dict[str, Any] = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode,
        'disable_web_page_preview': disable_web_page_preview,
    }
    if reply_markup is not None:
        payload['reply_markup'] = reply_markup
    status, _result = _post_status('sendMessage', json_payload=payload)
    return status


def send_photo_status(
    chat_id,
    photo,
    *,
    caption: str = '',
    parse_mode: str = 'HTML',
    filename: str = 'photo.png',
    reply_markup: dict | None = None,
) -> tuple[str, str]:
    """Send ``photo`` (bytes, or a file_id from an earlier send).

    Returns (status, file_id) so callers can reuse the upload.
    """
    import json

    data: dict[str, Any] = {'chat_id': chat_id}
    if caption:
        data['caption'] = caption
        data['parse_mode'] = parse_mode
    if reply_markup is not None:
        data['reply_markup'] = json.dumps(reply_markup)
    if isinstance(photo, str):
        data['photo'] = photo
        status, result = _post_status('sendPhoto', data=data)
    else:
        files = {'photo': (filename, photo, _photo_content_type(filename))}
        status, result = _post_status('sendPhoto', data=data, files=files)
    sizes = result.get('photo') or []
    file_id = sizes[-1].get('file_id', '') if sizes else ''
    return status, file_id


def _photo_content_type(filename: str) -> str:
    lower = (filename or '').lower()
    if lower.endswith(('.jpg', '.jpeg')):
        return 'image/jpeg'
    if lower.endswith('.webp'):
        return 'image/webp'
    if lower.endswith('.gif'):
        return 'image/gif'
    return 'image/png'


def send_photo(
    chat_id,
    photo_bytes: bytes,
    *,
    caption: str = '',
    parse_mode: str = 'HTML',
    filename: str = 'photo.png',
    reply_markup: dict | None = None,
) -> dict | None:
    """Send a PNG/JPEG photo. Returns Telegram API result dict on success."""
    data: dict[str, Any] = {'chat_id': chat_id}
    if caption:
        data['caption'] = caption
        data['parse_mode'] = parse_mode
    if reply_markup is not None:
        import json
        data['reply_markup'] = json.dumps(reply_markup)
    files = {'photo': (filename, photo_bytes, _photo_content_type(filename))}
    body = _post_multipart('sendPhoto', data, files)
    if not body:
        return None
    return body.get('result') or {}


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
    except Exception as error:
        logger.error('Telegram getWebhookInfo failed: %s', _safe_error_text(error))
    return None
