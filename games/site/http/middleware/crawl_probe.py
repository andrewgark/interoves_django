"""Log who walks the login-link / results-snapshot crawl space.

On 2026-11-19 these requests were over half of web traffic and the logs had
no user agent or client address to tell search engines from scrapers. One
compact line per matching request, nothing for ordinary pages.
"""

import json
import logging

from games.site.http.auth_observability import _request_ip_fields

logger = logging.getLogger('games.crawl_probe')

_UA_LIMIT = 300


def is_crawl_space_request(request) -> bool:
    if request.method != 'GET':
        return False
    path = request.path
    if path.startswith('/accounts/') and path.endswith('/login/'):
        return True
    return 'snapshot' in request.GET


class CrawlProbeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            if is_crawl_space_request(request):
                next_value = request.GET.get('next') or ''
                logger.info(json.dumps({
                    'event': 'crawl_probe',
                    'path': request.path[:120],
                    'status': response.status_code,
                    'ua': (request.META.get('HTTP_USER_AGENT') or '')[:_UA_LIMIT],
                    'ip': _request_ip_fields(request)['client_ip'],
                    'country': request.META.get('HTTP_CF_IPCOUNTRY') or '',
                    'authenticated': bool(getattr(getattr(request, 'user', None), 'is_authenticated', False)),
                    'has_cookie': bool(request.COOKIES),
                    'next_has_snapshot': 'snapshot' in next_value,
                    'escaped_amp': '&amp;' in next_value or 'amp=' in request.META.get('QUERY_STRING', ''),
                }, ensure_ascii=False, separators=(',', ':')))
        except Exception:
            pass
        return response
