"""Read the @interoveslocumpraesta feed via the Instagram Graph API.

Uses the "Instagram API with Instagram Login" product (host graph.instagram.com) with a
long-lived Instagram User access token. Only read access to the app's own media is needed,
so the sole permission required is ``instagram_business_basic`` (Standard Access, no App
Review, works while the app stays in Development mode with the account added as a Tester).

The long-lived token expires after 60 days and must be refreshed while still valid — see
``refresh_access_token`` and the ``instagram_refresh_token`` management command.
"""

from __future__ import annotations

import io
import logging
import time
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger('application')

GRAPH_BASE = 'https://graph.instagram.com'
# Content Publishing uses versioned paths on the same host.
GRAPH_VERSION = 'v21.0'
_USER_ID_CACHE_KEY = 'instagram_user_id_v1'
# Fields we pull per media item. children{...} lets us pick a display image for carousels.
MEDIA_FIELDS = (
    'id,caption,media_type,media_url,permalink,thumbnail_url,timestamp,'
    'children{media_url,media_type,thumbnail_url}'
)
_FEED_CACHE_KEY = 'instagram_feed_v1'
_HTTP_TIMEOUT = 15


def current_access_token() -> str:
    """The live access token: DB row (source of truth after refresh) with settings fallback.

    The DB is authoritative because the refresh cron writes the rotated token there; the
    INSTAGRAM_ACCESS_TOKEN env/settings value only seeds it (and covers the window before
    the InstagramToken table/row exists, e.g. during migrate).
    """
    try:
        from games.instagram.models import InstagramToken

        row = InstagramToken.get()
        if row and row.access_token:
            return row.access_token
    except Exception:
        logger.debug('InstagramToken lookup failed; using settings token', exc_info=True)
    return (getattr(settings, 'INSTAGRAM_ACCESS_TOKEN', '') or '').strip()


def instagram_configured() -> bool:
    return bool(current_access_token())


def _display_url(item: dict[str, Any]) -> str | None:
    """Best image URL to show for a media item (handles video + carousel)."""
    media_type = item.get('media_type')
    if media_type == 'VIDEO':
        return item.get('thumbnail_url') or item.get('media_url')
    if media_type == 'CAROUSEL_ALBUM':
        children = (item.get('children') or {}).get('data') or []
        for child in children:
            if child.get('media_type') == 'VIDEO':
                url = child.get('thumbnail_url') or child.get('media_url')
            else:
                url = child.get('media_url')
            if url:
                return url
        return item.get('thumbnail_url')
    return item.get('media_url')


def _normalize(item: dict[str, Any]) -> dict[str, Any]:
    caption = (item.get('caption') or '').strip()
    return {
        'id': item.get('id'),
        'caption': caption,
        'media_type': item.get('media_type'),
        'permalink': item.get('permalink'),
        'image_url': _display_url(item),
        'is_video': item.get('media_type') == 'VIDEO',
        'is_album': item.get('media_type') == 'CAROUSEL_ALBUM',
        'timestamp': item.get('timestamp'),
    }


def fetch_media(limit: int = 12, *, use_cache: bool = True) -> list[dict[str, Any]]:
    """Return a normalized list of recent posts, or [] on any failure.

    Results are cached for ``INSTAGRAM_FEED_CACHE_SECONDS`` so page views don't hit the
    Graph API each time. Never raises — a broken/expired token degrades to an empty feed.
    """
    if not instagram_configured():
        return []

    cache_key = f'{_FEED_CACHE_KEY}:{limit}'
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    try:
        response = requests.get(
            f'{GRAPH_BASE}/me/media',
            params={
                'fields': MEDIA_FIELDS,
                'limit': limit,
                'access_token': current_access_token(),
            },
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning('Instagram feed request failed: %s', exc)
        return cache.get(cache_key) or []

    if response.status_code >= 400:
        logger.warning(
            'Instagram feed error: %s %s', response.status_code, response.text[:500]
        )
        return cache.get(cache_key) or []

    data = response.json().get('data') or []
    items = [_normalize(item) for item in data]
    items = [item for item in items if item.get('image_url')]

    cache_seconds = getattr(settings, 'INSTAGRAM_FEED_CACHE_SECONDS', 600)
    cache.set(cache_key, items, cache_seconds)
    return items


def refresh_access_token() -> dict[str, Any]:
    """Exchange the current long-lived token for a fresh one (extends expiry ~60 days).

    Returns the API payload, e.g. {"access_token": "...", "token_type": "bearer",
    "expires_in": 5183944}. Raises RuntimeError on failure. The token must still be valid
    (and at least 24h old) for this to succeed, so run it well before expiry.
    """
    token = current_access_token()
    if not token:
        raise RuntimeError('No Instagram access token configured')

    response = requests.get(
        f'{GRAPH_BASE}/refresh_access_token',
        params={
            'grant_type': 'ig_refresh_token',
            'access_token': token,
        },
        timeout=_HTTP_TIMEOUT,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f'Instagram token refresh failed: {response.status_code} {response.text[:500]}'
        )
    return response.json()


def refresh_and_persist() -> dict[str, Any]:
    """Refresh the token and store the new value in the DB (source of truth). Returns payload."""
    from datetime import timedelta

    from django.utils import timezone

    from games.instagram.models import InstagramToken

    payload = refresh_access_token()
    new_token = payload.get('access_token')
    if not new_token:
        raise RuntimeError(f'Instagram refresh response missing access_token: {payload}')

    expires_in = payload.get('expires_in')
    row = InstagramToken.get() or InstagramToken()
    row.access_token = new_token
    row.expires_at = (
        timezone.now() + timedelta(seconds=int(expires_in)) if expires_in else None
    )
    row.save()
    clear_feed_cache()
    return payload


def clear_feed_cache() -> None:
    for limit in (3, 6, 9, 12, 24):
        cache.delete(f'{_FEED_CACHE_KEY}:{limit}')


# --- Content publishing (post to the feed) ---------------------------------

def publish_configured() -> bool:
    """Publishing needs the same token; content_publish scope is checked at call time."""
    return bool(current_access_token())


def get_user_id() -> str:
    """Instagram Professional account id for the token (needed for publish edges)."""
    explicit = (getattr(settings, 'INSTAGRAM_USER_ID', '') or '').strip()
    if explicit:
        return explicit
    cached = cache.get(_USER_ID_CACHE_KEY)
    if cached:
        return cached
    response = requests.get(
        f'{GRAPH_BASE}/me',
        params={'fields': 'id', 'access_token': current_access_token()},
        timeout=_HTTP_TIMEOUT,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f'Instagram /me failed: {response.status_code} {response.text[:300]}'
        )
    uid = str(response.json().get('id') or '')
    if not uid:
        raise RuntimeError(f'Instagram /me missing id: {response.text[:300]}')
    cache.set(_USER_ID_CACHE_KEY, uid, 24 * 3600)
    return uid


def png_to_instagram_jpeg(png_bytes: bytes, *, bg: tuple[int, int, int] = (247, 244, 239)) -> bytes:
    """Convert a PNG to a square JPEG on a solid background.

    Instagram's content-publishing API only accepts JPEG and requires an aspect ratio
    between 4:5 and 1.91:1; padding to a square (1:1) is always within range regardless
    of the teaser's dimensions.
    """
    from PIL import Image

    im = Image.open(io.BytesIO(png_bytes)).convert('RGB')
    w, h = im.size
    side = max(w, h)
    canvas = Image.new('RGB', (side, side), bg)
    canvas.paste(im, ((side - w) // 2, (side - h) // 2))
    out = io.BytesIO()
    canvas.save(out, format='JPEG', quality=90)
    return out.getvalue()


def publish_image_url(image_url: str, caption: str, *, wait_seconds: int = 60) -> str:
    """Publish a single image (by public URL) to the feed. Returns the new media id.

    Two-step flow: create a media container, wait until Instagram has fetched/processed
    the image (status FINISHED), then publish it. Raises RuntimeError on any failure.
    """
    if not publish_configured():
        raise RuntimeError('Instagram publishing not configured (no access token)')

    token = current_access_token()
    user_id = get_user_id()
    base = f'{GRAPH_BASE}/{GRAPH_VERSION}/{user_id}'

    create = requests.post(
        f'{base}/media',
        data={'image_url': image_url, 'caption': caption or '', 'access_token': token},
        timeout=60,
    )
    if create.status_code >= 400:
        raise RuntimeError(
            f'Instagram container create failed: {create.status_code} {create.text[:500]}'
        )
    creation_id = str(create.json().get('id') or '')
    if not creation_id:
        raise RuntimeError(f'Instagram container missing id: {create.text[:500]}')

    deadline = time.time() + wait_seconds
    status_code = None
    while time.time() < deadline:
        status = requests.get(
            f'{GRAPH_BASE}/{GRAPH_VERSION}/{creation_id}',
            params={'fields': 'status_code', 'access_token': token},
            timeout=30,
        )
        status_code = (status.json() or {}).get('status_code')
        if status_code == 'FINISHED':
            break
        if status_code in ('ERROR', 'EXPIRED'):
            raise RuntimeError(f'Instagram container {status_code}: {status.text[:500]}')
        time.sleep(2)
    if status_code != 'FINISHED':
        raise RuntimeError(f'Instagram container not ready (status={status_code})')

    publish = requests.post(
        f'{base}/media_publish',
        data={'creation_id': creation_id, 'access_token': token},
        timeout=60,
    )
    if publish.status_code >= 400:
        raise RuntimeError(
            f'Instagram publish failed: {publish.status_code} {publish.text[:500]}'
        )
    media_id = str(publish.json().get('id') or '')
    if not media_id:
        raise RuntimeError(f'Instagram publish missing media id: {publish.text[:500]}')
    clear_feed_cache()
    return media_id
