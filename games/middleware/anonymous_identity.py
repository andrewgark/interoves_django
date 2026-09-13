"""Bind server-issued anonymous identity on normal site requests."""


class AnonymousIdentityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from games.analytics_identity import (
            apply_anonymous_identity_cookies,
            bind_anonymous_identity,
        )

        bind_anonymous_identity(request)
        response = self.get_response(request)
        apply_anonymous_identity_cookies(request, response)
        return response
