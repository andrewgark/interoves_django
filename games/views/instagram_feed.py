"""Public page showing the @interoveslocumpraesta Instagram feed."""

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import render

from games.instagram.api import fetch_media, instagram_configured, png_to_instagram_jpeg


def instagram_feed(request):
    posts = fetch_media(limit=12) if instagram_configured() else []
    username = getattr(settings, 'INSTAGRAM_USERNAME', 'interoveslocumpraesta')
    return render(request, 'new/instagram_feed.html', {
        'page_title': 'Instagram',
        'posts': posts,
        'instagram_username': username,
        'instagram_profile_url': f'https://www.instagram.com/{username}/',
        'show_sections_nav': True,
    })


def ladder_teaser_jpg(request, number):
    """Public JPEG of a published ladder teaser — the image Instagram fetches on publish.

    Rendered from the ladder task (independent of any Telegram post record), padded to a
    square JPEG (Instagram content-publishing requires JPEG within a 4:5..1.91:1 ratio),
    and cached. 404 for unpublished/unknown numbers so future ladders aren't leaked.
    """
    from games.telegram.ladder_channel import resolve_ladder_by_number
    from games.telegram.ladder_image import render_ladder_teaser_png

    cache_key = f'ladder_teaser_jpg:{number}'
    data = cache.get(cache_key)
    if data is None:
        ladder = resolve_ladder_by_number(int(number))
        if ladder is None:
            raise Http404('ladder not published')
        png = render_ladder_teaser_png(ladder.task, ladder_number=ladder.number)
        data = png_to_instagram_jpeg(png)
        cache.set(cache_key, data, 3600)
    response = HttpResponse(data, content_type='image/jpeg')
    response['Cache-Control'] = 'public, max-age=3600'
    return response
