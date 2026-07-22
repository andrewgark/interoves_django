"""Support console helpers for SocialQueuePost."""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from games.social.models import SocialQueuePost
from games.social.publish import publish_instagram, publish_telegram, publish_twitter


class SocialSupportError(Exception):
    pass


def serialize_post(post: SocialQueuePost) -> dict:
    return {
        'id': post.pk,
        'caption': post.caption or '',
        'source': post.source,
        'ladder_number': post.ladder_number,
        'ladder_date': post.ladder_date.isoformat() if post.ladder_date else None,
        'play_url': post.play_url or '',
        'image_url': post.image.url if post.image else '',
        'created_at': post.created_at.isoformat() if post.created_at else '',
        'telegram': {
            'status': post.telegram_status,
            'external_id': post.telegram_external_id,
            'error': post.telegram_error,
            'at': post.telegram_at.isoformat() if post.telegram_at else '',
            'scheduled_for': (
                post.telegram_scheduled_for.isoformat()
                if post.telegram_scheduled_for else ''
            ),
        },
        'twitter': {
            'status': post.twitter_status,
            'external_id': post.twitter_external_id,
            'error': post.twitter_error,
            'at': post.twitter_at.isoformat() if post.twitter_at else '',
        },
        'instagram': {
            'status': post.instagram_status,
            'external_id': post.instagram_external_id,
            'error': post.instagram_error,
            'at': post.instagram_at.isoformat() if post.instagram_at else '',
        },
    }


def list_posts(limit: int = 100) -> list[dict]:
    qs = SocialQueuePost.objects.all()[:limit]
    return [serialize_post(p) for p in qs]


def get_post(post_id: int) -> SocialQueuePost:
    post = SocialQueuePost.objects.filter(pk=post_id).first()
    if post is None:
        raise SocialSupportError('Post not found')
    return post


def create_post(*, caption: str = '', image_file=None) -> SocialQueuePost:
    post = SocialQueuePost(
        source=SocialQueuePost.SOURCE_MANUAL,
        caption=(caption or '').strip(),
    )
    if image_file is not None:
        post.image = image_file
    post.save()
    return post


def update_post(
    post: SocialQueuePost,
    *,
    caption: str | None = None,
    image_file=None,
) -> SocialQueuePost:
    if caption is not None:
        post.caption = caption.strip()
    if image_file is not None:
        post.image = image_file
    post.save()
    return post


def _parse_schedule(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = parse_datetime(str(value).strip())
    if dt is None:
        raise SocialSupportError('Invalid schedule datetime')
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def publish_network(
    post: SocialQueuePost,
    network: str,
    *,
    force: bool = False,
    immediate: bool = True,
    schedule_at=None,
) -> SocialQueuePost:
    network = (network or '').strip().lower()
    if network == 'telegram':
        sched = _parse_schedule(schedule_at)
        return publish_telegram(
            post,
            immediate=immediate if sched is None else False,
            schedule_at=sched,
            force=force,
        )
    if network == 'twitter':
        return publish_twitter(post, force=force)
    if network == 'instagram':
        return publish_instagram(post, force=force)
    raise SocialSupportError('Unknown network: {}'.format(network))
