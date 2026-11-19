"""Turn AI crawlers away from the login-link / results-variant crawl space.

On 2026-11-19 Cloudflare analytics showed ``meta-externalagent`` (Meta's AI
crawler) behind nearly all requests to ``/accounts/<provider>/login/`` and
most results-table variants, without ever fetching robots.txt. Those
requests took admission permits from players. Answer them before the
admission gate and Django; ordinary pages stay open to the same crawlers.
"""

from __future__ import annotations

from urllib.parse import parse_qsl

_BLOCKED_AGENT_MARKERS = (b'meta-externalagent', b'reflectionbot')
_VARIANT_PARAMS = frozenset({'snapshot', 'sort', 'actors', 'page'})
_BODY = b'Crawling this URL space is not allowed; see /robots.txt.\n'


def _user_agent(scope) -> bytes:
    for name, value in scope.get('headers') or ():
        if name == b'user-agent':
            return value.lower()
    return b''


def is_blocked_crawl(scope) -> bool:
    if not any(marker in _user_agent(scope) for marker in _BLOCKED_AGENT_MARKERS):
        return False
    path = scope.get('path') or ''
    if path.startswith('/accounts/'):
        return True
    query = (scope.get('query_string') or b'').decode('latin-1')
    return any(key in _VARIANT_PARAMS for key, _value in parse_qsl(query, keep_blank_values=True))


class CrawlerGuardASGIMiddleware:
    def __init__(self, app):
        self.app = app
        self.blocked = 0

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'http' and is_blocked_crawl(scope):
            self.blocked += 1
            await send({
                'type': 'http.response.start',
                'status': 403,
                'headers': [
                    (b'content-type', b'text/plain; charset=utf-8'),
                    (b'content-length', str(len(_BODY)).encode('ascii')),
                    (b'cache-control', b'public, max-age=86400'),
                    (b'x-robots-tag', b'noindex, nofollow'),
                ],
            })
            await send({'type': 'http.response.body', 'body': _BODY, 'more_body': False})
            return
        return await self.app(scope, receive, send)
