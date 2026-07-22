"""Publish a SocialQueuePost to Telegram / X / Instagram."""

from __future__ import annotations

import logging
from datetime import datetime

from django.conf import settings
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
