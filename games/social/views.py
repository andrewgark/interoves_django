"""Public endpoints for SocialQueuePost media (Instagram fetch URL)."""

from __future__ import annotations

from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404

from games.instagram.api import to_instagram_jpeg
from games.social.models import SocialQueuePost


def social_queue_instagram_jpg(request, pk):
    """Public JPEG for Instagram Graph API to fetch on publish."""
    post = get_object_or_404(SocialQueuePost, pk=pk)
    if not post.image:
        raise Http404('no image')

    cache_key = 'social_queue_jpg:{}:{}'.format(post.pk, post.updated_at.timestamp())
    data = cache.get(cache_key)
    if data is None:
        data = to_instagram_jpeg(post.image_bytes())
        cache.set(cache_key, data, 3600)
    response = HttpResponse(data, content_type='image/jpeg')
    response['Cache-Control'] = 'public, max-age=3600'
    return response
