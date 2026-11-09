"""Bind server-issued anonymous identity on normal site requests."""

from games.site.http.middleware.request_timing import timing_phase


class AnonymousIdentityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from games.ops.analytics.acquisition import (
            apply_first_touch_cookie,
            bind_first_touch,
            release_first_touch,
        )
        from games.ops.analytics.identity import (
            apply_anonymous_identity_cookies,
            bind_anonymous_identity,
        )

        with timing_phase(request, 'anonymous_identity_pre'):
            identity = bind_anonymous_identity(request)
            # A browser the server identifies for the first time: remember
            # where it came from (games.ops.analytics.acquisition).
            touch_token = bind_first_touch(
                request,
                new_browser=identity is not None and identity.issue_key_cookie,
            )
        try:
            response = self.get_response(request)
        finally:
            release_first_touch(touch_token)
        with timing_phase(request, 'anonymous_identity_post'):
            apply_anonymous_identity_cookies(request, response)
            apply_first_touch_cookie(request, response)
        return response
