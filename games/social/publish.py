"""Publish a SocialQueuePost to Telegram / X / Instagram."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from games.instagram.api import publish_configured, publish_image_url
from games.social.models import SocialQueuePost
from games.telegram.config import channel_chat_id, telegram_channel_configured
from games.telegram.mtproto import (
    delete_channel_messages_sync,
    schedule_channel_photo_sync,
    telegram_user_configured,
)
from games.twitter.api import (
    html_caption_to_plain,
    post_tweet_with_image,
    twitter_configured,
)

logger = logging.getLogger('application')


def _plain_caption(post: SocialQueuePost) -> str:
    text = html_caption_to_plain(post.caption)
    if text:
        return text
    if post.ladder_number:
        return 'Лесенка №{}\n{}'.format(post.ladder_number, post.play_url)
    return post.caption or ''


def _filename(post: SocialQueuePost) -> str:
    if post.ladder_number:
        return 'ladder-{}.png'.format(post.ladder_number)
    return 'social-{}.png'.format(post.pk or 'draft')


def publish_telegram(
    post: SocialQueuePost,
    *,
    immediate: bool = False,
    schedule_at: datetime | None = None,
    force: bool = False,
) -> SocialQueuePost:
    """Post/schedule photo to the Telegram channel. Updates telegram_* fields."""
    if post.telegram_ok and post.telegram_external_id and not force:
        return post

    if not (telegram_user_configured() and telegram_channel_configured()):
        post.telegram_status = SocialQueuePost.STATUS_SKIPPED
        post.telegram_error = 'Telegram channel / user session not configured'
        post.save(update_fields=['telegram_status', 'telegram_error', 'updated_at'])
        return post

    data = post.image_bytes()
    if not data:
        post.telegram_status = SocialQueuePost.STATUS_FAILED
        post.telegram_error = 'No image on post'
        post.save(update_fields=['telegram_status', 'telegram_error', 'updated_at'])
        return post

    if force and post.telegram_external_id:
        try:
            delete_channel_messages_sync(
                chat=channel_chat_id(),
                message_ids=[int(post.telegram_external_id)],
            )
        except Exception:
            logger.exception(
                'Failed to delete previous telegram message_id=%s',
                post.telegram_external_id,
            )

    use_schedule = None if immediate else schedule_at
    try:
        result = schedule_channel_photo_sync(
            chat=channel_chat_id(),
            photo_bytes=data,
            caption=post.caption or '',
            schedule_at=use_schedule,
            filename=_filename(post),
        )
    except Exception as exc:
        logger.exception('Telegram publish failed for social post pk=%s', post.pk)
        post.telegram_status = SocialQueuePost.STATUS_FAILED
        post.telegram_error = str(exc)[:500]
        post.telegram_scheduled_for = use_schedule
        post.save(update_fields=[
            'telegram_status', 'telegram_error', 'telegram_scheduled_for', 'updated_at',
        ])
        return post

    if immediate or use_schedule is None:
        post.telegram_status = SocialQueuePost.STATUS_SENT
        post.telegram_at = timezone.now()
    else:
        post.telegram_status = SocialQueuePost.STATUS_SCHEDULED
        post.telegram_at = timezone.now()
    post.telegram_external_id = str(result.get('message_id') or '')
    post.telegram_error = ''
    post.telegram_scheduled_for = use_schedule
    post.save(update_fields=[
        'telegram_status',
        'telegram_external_id',
        'telegram_error',
        'telegram_at',
        'telegram_scheduled_for',
        'updated_at',
    ])
    return post


def queue_network(post: SocialQueuePost, network: str, run_at: datetime) -> SocialQueuePost:
    """Put a network on the internal schedule (status=queued)."""
    network = (network or '').strip().lower()
    if timezone.is_naive(run_at):
        run_at = timezone.make_aware(run_at, timezone.get_current_timezone())
    if network == 'telegram':
        post.telegram_status = SocialQueuePost.STATUS_QUEUED
        post.telegram_queued_for = run_at
        post.telegram_error = ''
        post.save(update_fields=[
            'telegram_status', 'telegram_queued_for', 'telegram_error', 'updated_at',
        ])
        return post
    if network == 'twitter':
        post.twitter_status = SocialQueuePost.STATUS_QUEUED
        post.twitter_queued_for = run_at
        post.twitter_error = ''
        post.save(update_fields=[
            'twitter_status', 'twitter_queued_for', 'twitter_error', 'updated_at',
        ])
        return post
    if network == 'instagram':
        post.instagram_status = SocialQueuePost.STATUS_QUEUED
        post.instagram_queued_for = run_at
        post.instagram_error = ''
        post.save(update_fields=[
            'instagram_status', 'instagram_queued_for', 'instagram_error', 'updated_at',
        ])
        return post
    raise ValueError('Unknown network: {}'.format(network))


def _publish_one_queued(network: str, pk: int) -> bool:
    """Publish a single queued post to one network. Returns True on success.

    Each publish re-fetches its own post instance and only writes that network's
    columns, so concurrent work on the same post across networks does not clobber.
    """
    post = SocialQueuePost.objects.filter(pk=pk).first()
    if post is None:
        return False
    if network == 'telegram':
        publish_telegram(post, immediate=True, force=False)
    elif network == 'twitter':
        publish_twitter(post, force=False)
    elif network == 'instagram':
        publish_instagram(post, force=False)
    else:
        raise ValueError('Unknown network: {}'.format(network))
    return True


def _publish_one_queued_worker(network: str, pk: int) -> bool:
    """Thread-pool entrypoint: closes the thread-local DB connection on exit.

    Django only auto-closes connections opened on the request thread, so pool
    threads would otherwise leak a connection per task.
    """
    try:
        return _publish_one_queued(network, pk)
    finally:
        connection.close()


def process_social_queue_tick(now: datetime | None = None) -> dict[str, Any]:
    """Publish networks whose internal queued_for time has arrived.

    Each post/network publish is an independent, I/O-bound external call, so we
    fan them out over a bounded thread pool instead of publishing serially. The
    worker count is capped by SOCIAL_QUEUE_MAX_WORKERS (default 8). SQLite cannot
    handle concurrent writers, so we fall back to inline serial publishing there
    (covers the test DB); production runs on Postgres and parallelizes.
    """
    now = now or timezone.now()
    stats = {'telegram': 0, 'twitter': 0, 'instagram': 0, 'errors': 0}

    def _queued_pks(status_field: str, queued_field: str) -> list[int]:
        return list(
            SocialQueuePost.objects.filter(
                **{
                    status_field: SocialQueuePost.STATUS_QUEUED,
                    '{}__lte'.format(queued_field): now,
                }
            ).values_list('pk', flat=True)[:50]
        )

    tasks: list[tuple[str, int]] = []
    for pk in _queued_pks('telegram_status', 'telegram_queued_for'):
        tasks.append(('telegram', pk))
    for pk in _queued_pks('twitter_status', 'twitter_queued_for'):
        tasks.append(('twitter', pk))
    for pk in _queued_pks('instagram_status', 'instagram_queued_for'):
        tasks.append(('instagram', pk))

    if not tasks:
        return stats

    max_workers = max(1, int(getattr(settings, 'SOCIAL_QUEUE_MAX_WORKERS', 8)))
    max_workers = min(max_workers, len(tasks))
    if connection.vendor == 'sqlite':
        max_workers = 1

    if max_workers == 1:
        for network, pk in tasks:
            try:
                if _publish_one_queued(network, pk):
                    stats[network] += 1
            except Exception:
                logger.exception('Social queue tick %s failed pk=%s', network, pk)
                stats['errors'] += 1
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {
                executor.submit(_publish_one_queued_worker, network, pk): (network, pk)
                for network, pk in tasks
            }
            for future in as_completed(future_to_task):
                network, pk = future_to_task[future]
                try:
                    if future.result():
                        stats[network] += 1
                except Exception:
                    logger.exception('Social queue tick %s failed pk=%s', network, pk)
                    stats['errors'] += 1

    if stats['telegram'] or stats['twitter'] or stats['instagram'] or stats['errors']:
        logger.info('Social queue tick: %s', stats)
    return stats


def publish_twitter(post: SocialQueuePost, *, force: bool = False) -> SocialQueuePost:
    if post.twitter_external_id and not force:
        return post
    if post.twitter_status == SocialQueuePost.STATUS_SENT and not force:
        return post

    if not twitter_configured():
        post.twitter_status = SocialQueuePost.STATUS_SKIPPED
        post.twitter_error = 'TWITTER_* credentials not configured'
        post.save(update_fields=['twitter_status', 'twitter_error', 'updated_at'])
        return post

    data = post.image_bytes()
    if not data:
        post.twitter_status = SocialQueuePost.STATUS_FAILED
        post.twitter_error = 'No image on post'
        post.save(update_fields=['twitter_status', 'twitter_error', 'updated_at'])
        return post

    try:
        result = post_tweet_with_image(
            text=_plain_caption(post),
            image_bytes=data,
            filename=_filename(post),
        )
        tweet_id = str((result.get('data') or {}).get('id') or '')
        if not tweet_id:
            raise RuntimeError('Twitter response missing tweet id: {}'.format(result)[:400])
        post.twitter_status = SocialQueuePost.STATUS_SENT
        post.twitter_external_id = tweet_id
        post.twitter_error = ''
        post.twitter_at = timezone.now()
        post.save(update_fields=[
            'twitter_status', 'twitter_external_id', 'twitter_error', 'twitter_at', 'updated_at',
        ])
    except Exception as exc:
        logger.exception('Twitter publish failed for social post pk=%s', post.pk)
        post.twitter_status = SocialQueuePost.STATUS_FAILED
        post.twitter_error = str(exc)[:500]
        post.save(update_fields=['twitter_status', 'twitter_error', 'updated_at'])
    return post


def publish_instagram(post: SocialQueuePost, *, force: bool = False) -> SocialQueuePost:
    if post.instagram_external_id and not force:
        return post
    if post.instagram_status == SocialQueuePost.STATUS_SENT and not force:
        return post

    if not publish_configured():
        post.instagram_status = SocialQueuePost.STATUS_SKIPPED
        post.instagram_error = 'INSTAGRAM_ACCESS_TOKEN not configured'
        post.save(update_fields=['instagram_status', 'instagram_error', 'updated_at'])
        return post

    if not post.image:
        post.instagram_status = SocialQueuePost.STATUS_FAILED
        post.instagram_error = 'No image on post'
        post.save(update_fields=['instagram_status', 'instagram_error', 'updated_at'])
        return post

    image_url = settings.SITE_BASE_URL + reverse(
        'social_queue_instagram_jpg', args=[post.pk]
    )
    try:
        media_id = publish_image_url(image_url, _plain_caption(post))
        post.instagram_status = SocialQueuePost.STATUS_SENT
        post.instagram_external_id = media_id
        post.instagram_error = ''
        post.instagram_at = timezone.now()
        post.save(update_fields=[
            'instagram_status',
            'instagram_external_id',
            'instagram_error',
            'instagram_at',
            'updated_at',
        ])
    except Exception as exc:
        logger.exception('Instagram publish failed for social post pk=%s', post.pk)
        post.instagram_status = SocialQueuePost.STATUS_FAILED
        post.instagram_error = str(exc)[:500]
        post.save(update_fields=['instagram_status', 'instagram_error', 'updated_at'])
    return post
