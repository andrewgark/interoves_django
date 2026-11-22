"""Site error handlers wired as ``handler404`` in the root URLconf."""

from django.http import HttpResponseNotFound
from django.template import loader


def page_not_found(request, exception=None):
    """Render 404.html without the request, so no context processor runs.

    Django's default handler renders with the request. When a context
    processor then raises (section-nav queries, analytics goals, ...), Django
    swallows the exception without a traceback and answers 500 instead: from
    Nov 21, 2026, bot probes such as /sitemap_index.xml/ sometimes raised
    «Критическая ошибка сайта» alerts this way. The template needs only
    {% static %}.
    """
    return HttpResponseNotFound(loader.render_to_string('404.html'))
