"""Public endpoints for SocialQueuePost media fetched by Meta APIs."""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404

from games.instagram.api import to_instagram_jpeg
from games.social.models import SocialQueuePost

logger = logging.getLogger('application')


def _read_social_image(post: SocialQueuePost) -> bytes:
    try:
        return post.social_image_bytes()
    except FileNotFoundError:
        return b''


def _restore_missing_word_salad_image(post: SocialQueuePost) -> bool:
    """Rebuild a salad teaser when the worker saved it only on local disk.

    The integrations worker does not write the public S3 bucket. Instagram
    fetches this URL from the web tier, which does.
    """
    if post.source != SocialQueuePost.SOURCE_WORD_SALAD or not post.ladder_number:
        return False
    from games.telegram.word_salad_channel import resolve_salad_by_number
    from games.telegram.word_salad_image import render_word_salad_teaser_png

    salad = resolve_salad_by_number(post.ladder_number)
    if salad is None:
        return False
    try:
        png = render_word_salad_teaser_png(
            salad.task,
            salad_number=salad.number,
            url=salad.play_url,
            fallback_to_pillow=False,
        )
    except Exception:
        logger.exception('Failed to rebuild social image for post pk=%s', post.pk)
        return False
    if not png:
        return False
    post.set_image_bytes(png, filename='salad-{}.png'.format(post.ladder_number))
    post.save(update_fields=['image', 'updated_at'])
    return True


def social_queue_instagram_jpg(request, pk):
    """Public JPEG for Instagram Graph API to fetch on publish."""
    post = get_object_or_404(SocialQueuePost, pk=pk)
    if not (post.social_image or post.image):
        raise Http404('no image')

    cache_key = 'social_queue_jpg:{}:{}'.format(post.pk, post.updated_at.timestamp())
    data = cache.get(cache_key)
    if data is None:
        raw = _read_social_image(post)
        if not raw and _restore_missing_word_salad_image(post):
            post.refresh_from_db()
            raw = _read_social_image(post)
            cache_key = 'social_queue_jpg:{}:{}'.format(post.pk, post.updated_at.timestamp())
        if not raw:
            raise Http404('image file missing')
        data = to_instagram_jpeg(raw)
        cache.set(cache_key, data, 3600)
    response = HttpResponse(data, content_type='image/jpeg')
    response['Cache-Control'] = 'public, max-age=3600'
    return response
