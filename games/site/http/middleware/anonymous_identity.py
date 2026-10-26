"""Bind server-issued anonymous identity on normal site requests."""

from games.site.http.middleware.request_timing import timing_phase


class AnonymousIdentityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from games.ops.analytics.identity import (
            apply_anonymous_identity_cookies,
            bind_anonymous_identity,
        )

        with timing_phase(request, 'anonymous_identity_pre'):
            bind_anonymous_identity(request)
        response = self.get_response(request)
        with timing_phase(request, 'anonymous_identity_post'):
            apply_anonymous_identity_cookies(request, response)
        return response
