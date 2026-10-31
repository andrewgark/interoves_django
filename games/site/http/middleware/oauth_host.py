"""Keep OAuth flows on a public hostname registered with every provider.

allauth builds ``redirect_uri`` from the request host, both when sending the
browser to the provider and when exchanging the code for a token, so a login
started on ru.interoves.com returns to ru.interoves.com. The origin also
accepts EB hostnames and instance IPs (health checks, direct access); an
OAuth flow started there would ask the provider for an unregistered
``redirect_uri``. Login starts on such hosts move to the main site; a
callback there is refused, since its state lives in another host's session.
"""

from __future__ import annotations

import re

from django.conf import settings
from django.http import HttpResponseBadRequest, HttpResponseRedirect

# /accounts/<provider>/login/, /accounts/<provider>/login/callback/ and the
# Telegram OIDC /accounts/telegram/callback/.
_OAUTH_PATH_RE = re.compile(r'^/accounts/[a-z0-9_-]+/(login/(?P<cb1>callback/)?|(?P<cb2>callback/))$')


class OAuthHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        hosts = getattr(settings, 'OAUTH_CALLBACK_HOSTS', ())
        match = _OAUTH_PATH_RE.match(request.path) if hosts else None
        if match is None or request.get_host() in hosts:
            return self.get_response(request)
        if match.group('cb1') or match.group('cb2'):
            return HttpResponseBadRequest('OAuth callback on an unregistered host.')
        return HttpResponseRedirect(settings.SITE_BASE_URL + request.get_full_path())
