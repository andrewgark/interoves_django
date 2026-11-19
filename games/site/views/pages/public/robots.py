"""robots.txt for the main site."""

from django.http import HttpResponse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

# Results tables multiply into snapshot/sort/filter/page variants, each with
# login links carrying the full URL as ``next``. Crawlers walking that space
# were ~30% of web requests (2026-11-18), each holding an admission permit.
ROBOTS_TXT = """User-agent: *
Disallow: /accounts/
Disallow: /admin/
Disallow: /console/
Disallow: /support/
Disallow: /anon-migrate-count/
Disallow: /anon-merge-jobs/
Disallow: /*?*snapshot=
Disallow: /*?*sort=
Disallow: /*?*actors=
Disallow: /*?*page=
"""


@require_GET
@cache_control(public=True, max_age=86400)
def robots_txt(request):
    return HttpResponse(ROBOTS_TXT, content_type='text/plain; charset=utf-8')


def root_icon(request, path):
    """Browsers and iOS ask for icons at the site root, not the <link> URLs.

    Without these routes APPEND_SLASH turned /favicon.ico into a 404 at
    /favicon.ico/. The static URL carries the release hash, so redirect
    temporarily instead of pinning one release.
    """
    from django.shortcuts import redirect
    from django.templatetags.static import static

    return redirect(static(path), permanent=False)
