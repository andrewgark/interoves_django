"""Post media tweets to @interoves via X API (OAuth 1.0a)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from typing import Any
from urllib.parse import quote

import requests
from django.conf import settings

logger = logging.getLogger('application')

UPLOAD_URL = 'https://upload.twitter.com/1.1/media/upload.json'
TWEET_URL = 'https://api.twitter.com/2/tweets'


def twitter_configured() -> bool:
    return bool(
        getattr(settings, 'TWITTER_API_KEY', '')
        and getattr(settings, 'TWITTER_API_SECRET', '')
        and getattr(settings, 'TWITTER_ACCESS_TOKEN', '')
        and getattr(settings, 'TWITTER_ACCESS_TOKEN_SECRET', '')
    )


def html_caption_to_plain(caption: str) -> str:
    """Strip Telegram HTML tags for X text."""
    text = re.sub(r'<br\s*/?>', '\n', caption or '', flags=re.I)
    text = re.sub(r'</p\s*>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = (
        text.replace('&lt;', '<')
        .replace('&gt;', '>')
        .replace('&amp;', '&')
        .replace('&quot;', '"')
        .replace('&#39;', "'")
    )
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    # Collapse excessive blank lines
    out: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                out.append('')
            blank = True
        else:
            out.append(line)
            blank = False
    return '\n'.join(out).strip()


def post_tweet_with_image(*, text: str, image_bytes: bytes, filename: str = 'image.png') -> dict[str, Any]:
    """
    Upload PNG and create a tweet. Returns API payload with data.id.
    Raises RuntimeError on failure.
    """
    if not twitter_configured():
        raise RuntimeError('Twitter API credentials not configured')

    media_id = _upload_media(image_bytes, filename=filename)
    payload: dict[str, Any] = {
        'text': text,
        'media': {'media_ids': [media_id]},
    }
    return _oauth_json_request('POST', TWEET_URL, json_body=payload)


def _percent_encode(value: str) -> str:
    return quote(str(value), safe='~')


def _oauth_header(
    *,
    method: str,
    url: str,
    extra_params: dict[str, str] | None = None,
) -> str:
    oauth: dict[str, str] = {
        'oauth_consumer_key': settings.TWITTER_API_KEY,
        'oauth_nonce': secrets.token_hex(16),
        'oauth_signature_method': 'HMAC-SHA1',
        'oauth_timestamp': str(int(time.time())),
        'oauth_token': settings.TWITTER_ACCESS_TOKEN,
        'oauth_version': '1.0',
    }
    params = dict(oauth)
    if extra_params:
        params.update(extra_params)
    base_items = sorted(
        (_percent_encode(k), _percent_encode(v)) for k, v in params.items()
    )
    param_string = '&'.join(f'{k}={v}' for k, v in base_items)
    base_string = '&'.join(
        [method.upper(), _percent_encode(url), _percent_encode(param_string)]
    )
    signing_key = (
        f'{_percent_encode(settings.TWITTER_API_SECRET)}'
        f'&{_percent_encode(settings.TWITTER_ACCESS_TOKEN_SECRET)}'
    )
    digest = hmac.new(
        signing_key.encode('utf-8'),
        base_string.encode('utf-8'),
        hashlib.sha1,
    ).digest()
    oauth['oauth_signature'] = base64.b64encode(digest).decode('ascii')
    parts = [
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth.items())
    ]
    return 'OAuth ' + ', '.join(parts)


def _upload_media(image_bytes: bytes, *, filename: str) -> str:
    header = _oauth_header(method='POST', url=UPLOAD_URL)
    response = requests.post(
        UPLOAD_URL,
        headers={'Authorization': header},
        files={'media': (filename, image_bytes)},
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f'Twitter media upload failed: {response.status_code} {response.text[:500]}'
        )
    media_id = response.json().get('media_id_string')
    if not media_id:
        raise RuntimeError(f'Twitter media upload missing media_id: {response.text[:500]}')
    return str(media_id)


def _oauth_json_request(method: str, url: str, *, json_body: dict) -> dict[str, Any]:
    header = _oauth_header(method=method, url=url)
    response = requests.request(
        method,
        url,
        headers={'Authorization': header, 'Content-Type': 'application/json'},
        data=json.dumps(json_body),
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f'Twitter API error: {response.status_code} {response.text[:800]}'
        )
    return response.json()
