"""Publish image posts through Meta's Threads API."""

from __future__ import annotations

import time
from typing import Any

import requests
from django.conf import settings

GRAPH_BASE = 'https://graph.threads.net'
_HTTP_TIMEOUT = 30


def threads_configured() -> bool:
    return bool((getattr(settings, 'THREADS_ACCESS_TOKEN', '') or '').strip())


def _request(method: str, path: str, **kwargs) -> dict[str, Any]:
    token = (getattr(settings, 'THREADS_ACCESS_TOKEN', '') or '').strip()
    params = dict(kwargs.pop('params', {}) or {})
    params['access_token'] = token
    response = requests.request(
        method, GRAPH_BASE + path, params=params, timeout=_HTTP_TIMEOUT, **kwargs
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f'Threads API error: {response.status_code} {response.text[:800]}'
        )
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f'Threads API returned invalid JSON: {response.text[:500]}') from exc


def publish_image_url(
    image_url: str,
    text: str,
    *,
    wait_seconds: int = 60,
    poll_seconds: float = 2,
) -> str:
    """Create and publish one IMAGE container; return the published thread id."""
    if not threads_configured():
        raise RuntimeError('Threads publishing not configured (no access token)')
    created = _request('POST', '/me/threads', data={
        'media_type': 'IMAGE',
        'image_url': image_url,
        'text': text,
    })
    creation_id = str(created.get('id') or '')
    if not creation_id:
        raise RuntimeError(f'Threads response missing container id: {created}')

    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        status = _request('GET', f'/{creation_id}', params={'fields': 'status'}).get('status')
        if status in ('FINISHED', 'PUBLISHED'):
            break
        if status in ('ERROR', 'EXPIRED'):
            raise RuntimeError(f'Threads media container status: {status}')
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Threads media container did not finish: {status or "unknown"}')
        time.sleep(poll_seconds)

    published = _request('POST', '/me/threads_publish', data={'creation_id': creation_id})
    thread_id = str(published.get('id') or '')
    if not thread_id:
        raise RuntimeError(f'Threads response missing post id: {published}')
    return thread_id
