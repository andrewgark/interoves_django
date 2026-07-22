"""Support console helpers for SocialQueuePost."""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from games.social.models import SocialQueuePost
from games.social.publish import (
    publish_instagram,
    publish_telegram,
    publish_twitter,
    queue_network as publish_queue_network,
)


class SocialSupportError(Exception):
    pass


def _net_blob(status, external_id, error, at, queued_for=None, scheduled_for=None):
    return {
        'status': status,
        'external_id': external_id,
        'error': error,
        'at': at.isoformat() if at else '',
        'queued_for': queued_for.isoformat() if queued_for else '',
        'scheduled_for': scheduled_for.isoformat() if scheduled_for else '',
    }


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
        'telegram': _net_blob(
            post.telegram_status,
            post.telegram_external_id,
            post.telegram_error,
            post.telegram_at,
            queued_for=post.telegram_queued_for,
            scheduled_for=post.telegram_scheduled_for,
        ),
        'twitter': _net_blob(
            post.twitter_status,
            post.twitter_external_id,
            post.twitter_error,
            post.twitter_at,
            queued_for=post.twitter_queued_for,
        ),
        'instagram': _net_blob(
            post.instagram_status,
            post.instagram_external_id,
            post.instagram_error,
            post.instagram_at,
            queued_for=post.instagram_queued_for,
        ),
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


def create_post_with_plan(
    *,
    caption: str = '',
    image_file=None,
    networks: list[str] | None = None,
    mode: str = 'draft',
    schedule_at=None,
) -> SocialQueuePost:
    """
    Create a draft, optionally schedule/publish to selected networks.

    mode:
      - draft: save only (default; never posts)
      - now: publish selected networks immediately
      - internal: queue selected on internal schedule (needs schedule_at)
      - tg_defer: Telegram → native deferred; other selected → internal queue
        (needs schedule_at)
    """
    post = create_post(caption=caption, image_file=image_file)
    mode = (mode or 'draft').strip().lower()
    if mode == 'draft':
        return post

    selected = {
        (n or '').strip().lower()
        for n in (networks or [])
        if (n or '').strip().lower() in ('telegram', 'twitter', 'instagram')
    }
    if not selected:
        raise SocialSupportError('Выберите хотя бы одну соцсеть')

    sched = _parse_schedule(schedule_at)
    if mode in ('internal', 'tg_defer') and sched is None:
        raise SocialSupportError('Укажите дату и время')

    if mode == 'now':
        for network in selected:
            publish_network(post, network, force=False, immediate=True, action='publish')
            post.refresh_from_db()
        return post

    if mode == 'internal':
        for network in selected:
            publish_network(
                post, network, action='queue', schedule_at=sched, force=False,
            )
            post.refresh_from_db()
        return post

    if mode == 'tg_defer':
        if 'telegram' not in selected:
            raise SocialSupportError('Режим «отложенные TG» требует Telegram')
        publish_network(
            post, 'telegram', action='tg_defer', schedule_at=sched, force=False,
        )
        post.refresh_from_db()
        for network in selected - {'telegram'}:
            publish_network(
                post, network, action='queue', schedule_at=sched, force=False,
            )
            post.refresh_from_db()
        return post

    raise SocialSupportError('Неизвестный mode: {}'.format(mode))


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


def delete_post(post: SocialQueuePost) -> None:
    if post.image:
        post.image.delete(save=False)
    post.delete()


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
    action: str = 'publish',
) -> SocialQueuePost:
    """
    action:
      - publish: send now (or TG native deferred if schedule_at and not immediate)
      - queue: put on internal schedule (requires schedule_at / queued_for)
      - tg_defer: Telegram native deferred (requires schedule_at)
    """
    network = (network or '').strip().lower()
    action = (action or 'publish').strip().lower()
    sched = _parse_schedule(schedule_at)

    if action == 'queue':
        if sched is None:
            raise SocialSupportError('Нужна дата/время для внутренней очереди')
        if network not in ('telegram', 'twitter', 'instagram'):
            raise SocialSupportError('Unknown network: {}'.format(network))
        return publish_queue_network(post, network, sched)

    if action == 'tg_defer':
        if network != 'telegram':
            raise SocialSupportError('tg_defer только для telegram')
        if sched is None:
            raise SocialSupportError('Нужна дата/время для отложенных Telegram')
        return publish_telegram(post, immediate=False, schedule_at=sched, force=force)

    if network == 'telegram':
        if sched is not None and not immediate:
            return publish_telegram(post, immediate=False, schedule_at=sched, force=force)
        return publish_telegram(post, immediate=True, force=force)
    if network == 'twitter':
        return publish_twitter(post, force=force)
    if network == 'instagram':
        return publish_instagram(post, force=force)
    raise SocialSupportError('Unknown network: {}'.format(network))
