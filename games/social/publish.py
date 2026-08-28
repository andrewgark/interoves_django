"""Publish a SocialQueuePost to Telegram / X / Instagram."""

from __future__ import annotations

import logging
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.models import F, Q
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

QUEUE_CLAIM_TIMEOUT = timedelta(
    minutes=max(1, int(getattr(settings, 'SOCIAL_QUEUE_CLAIM_TIMEOUT_MINUTES', 30)))
)
SOCIAL_QUEUE_MAX_ATTEMPTS = max(
    1, int(getattr(settings, 'SOCIAL_QUEUE_MAX_ATTEMPTS', 3))
)
SOCIAL_QUEUE_RETRY_DELAY = timedelta(
    minutes=max(1, int(getattr(settings, 'SOCIAL_QUEUE_RETRY_DELAY_MINUTES', 5)))
)
_UNSET = object()


def _telegram_log_fields(post_id: int) -> tuple[str, str]:
    source = SocialQueuePost.objects.filter(pk=post_id).values_list('source', flat=True).first()
    return socket.gethostname(), source or 'unknown'


def claim_telegram_post(
    post_id: int,
    *,
    now: datetime | None = None,
    queued_only: bool = False,
    force: bool = False,
) -> str | None:
    """Atomically lease one Telegram delivery and return its fencing token."""
    now = now or timezone.now()
    stale_before = now - QUEUE_CLAIM_TIMEOUT
    token = uuid.uuid4().hex

    base = SocialQueuePost.objects.filter(pk=post_id)
    if queued_only:
        base = base.filter(telegram_external_id='', telegram_queued_for__lte=now)
        available = base.filter(telegram_status=SocialQueuePost.STATUS_QUEUED)
    elif force:
        available = base.exclude(telegram_status=SocialQueuePost.STATUS_PUBLISHING)
    else:
        base = base.filter(telegram_external_id='')
        available = base.filter(
            telegram_status__in=(
                SocialQueuePost.STATUS_PENDING,
                SocialQueuePost.STATUS_FAILED,
            )
        )

    updates = {
        'telegram_status': SocialQueuePost.STATUS_PUBLISHING,
        'telegram_error': '',
        'telegram_claimed_at': now,
        'telegram_claim_token': token,
        'updated_at': now,
    }
    stale_reclaimed = False
    claimed = available.update(**updates)
    if claimed != 1:
        stale = base.filter(telegram_status=SocialQueuePost.STATUS_PUBLISHING).filter(
            Q(telegram_claimed_at__isnull=True)
            | Q(telegram_claimed_at__lt=stale_before)
        )
        claimed = stale.update(**updates)
        stale_reclaimed = claimed == 1

    host, source = _telegram_log_fields(post_id)
    if claimed == 1:
        logger.info(
            'Telegram claim acquired host=%s post_id=%s source=%s network=telegram '
            'token=%s stale_reclaimed=%s',
            host,
            post_id,
            source,
            token[:8],
            stale_reclaimed,
        )
        return token

    logger.info(
        'Telegram claim denied host=%s post_id=%s source=%s network=telegram',
        host,
        post_id,
        source,
    )
    return None


def telegram_claim_is_owned(post_id: int, claim_token: str) -> bool:
    return SocialQueuePost.objects.filter(
        pk=post_id,
        telegram_status=SocialQueuePost.STATUS_PUBLISHING,
        telegram_claim_token=claim_token,
    ).exists()


def update_claimed_telegram_post(post_id: int, claim_token: str, **updates) -> bool:
    """Update pre-send payload fields without allowing a stale owner to write."""
    updates['updated_at'] = timezone.now()
    return SocialQueuePost.objects.filter(
        pk=post_id,
        telegram_status=SocialQueuePost.STATUS_PUBLISHING,
        telegram_claim_token=claim_token,
    ).update(**updates) == 1


def _begin_telegram_attempt(post_id: int, claim_token: str) -> bool:
    """Count an actual Telegram API send only while this worker owns the lease."""
    return SocialQueuePost.objects.filter(
        pk=post_id,
        telegram_status=SocialQueuePost.STATUS_PUBLISHING,
        telegram_claim_token=claim_token,
    ).update(
        telegram_attempts=F('telegram_attempts') + 1,
        updated_at=timezone.now(),
    ) == 1


def complete_telegram_publish(
    post_id: int,
    claim_token: str,
    *,
    status: str,
    error: str = '',
    external_id: str | object = _UNSET,
    telegram_at: datetime | None | object = _UNSET,
    scheduled_for: datetime | None | object = _UNSET,
) -> bool:
    """Finish a Telegram delivery only if the caller still owns its claim."""
    updates: dict[str, Any] = {
        'telegram_status': status,
        'telegram_error': error,
        'telegram_claimed_at': None,
        'telegram_claim_token': '',
        'updated_at': timezone.now(),
    }
    if external_id is not _UNSET:
        updates['telegram_external_id'] = external_id
    if telegram_at is not _UNSET:
        updates['telegram_at'] = telegram_at
    if scheduled_for is not _UNSET:
        updates['telegram_scheduled_for'] = scheduled_for

    completed = SocialQueuePost.objects.filter(
        pk=post_id,
        telegram_status=SocialQueuePost.STATUS_PUBLISHING,
        telegram_claim_token=claim_token,
    ).update(**updates) == 1
    host, source = _telegram_log_fields(post_id)
    if completed:
        logger.info(
            'Telegram claim completed host=%s post_id=%s source=%s network=telegram '
            'token=%s status=%s message_id=%s',
            host,
            post_id,
            source,
            claim_token[:8],
            status,
            '' if external_id is _UNSET else external_id,
        )
    else:
        logger.warning(
            'Telegram stale completion rejected host=%s post_id=%s source=%s '
            'network=telegram token=%s status=%s',
            host,
            post_id,
            source,
            claim_token[:8],
            status,
        )
    return completed


def _network_fields(network: str) -> tuple[str, str, str, str]:
    network = (network or '').strip().lower()
    if network == 'telegram':
        return ('telegram_status', 'telegram_queued_for', 'telegram_error', 'telegram_external_id')
    if network == 'twitter':
        return ('twitter_status', 'twitter_queued_for', 'twitter_error', 'twitter_external_id')
    if network == 'instagram':
        return ('instagram_status', 'instagram_queued_for', 'instagram_error', 'instagram_external_id')
    raise ValueError('Unknown network: {}'.format(network))


def _claim_queued_post(network: str, pk: int, now: datetime) -> bool | str:
    """Atomically claim one queued network publish across multiple app instances."""
    if network == 'telegram':
        return claim_telegram_post(pk, now=now, queued_only=True)
    status_field, queued_field, error_field, external_id_field = _network_fields(network)
    stale_before = now - QUEUE_CLAIM_TIMEOUT
    return SocialQueuePost.objects.filter(
        pk=pk,
        **{
            external_id_field: '',
            '{}__lte'.format(queued_field): now,
        }
    ).filter(
        Q(**{status_field: SocialQueuePost.STATUS_QUEUED})
        | (
            Q(**{status_field: SocialQueuePost.STATUS_PUBLISHING})
            & Q(updated_at__lt=stale_before)
        )
    ).update(
        **{
            status_field: SocialQueuePost.STATUS_PUBLISHING,
            error_field: '',
        }
    ) == 1


def _plain_caption(post: SocialQueuePost) -> str:
    text = html_caption_to_plain(post.caption)
    if text:
        return text
    if post.source == SocialQueuePost.SOURCE_WORD_SALAD and post.ladder_number:
        return 'Салатик №{}\n{}'.format(post.ladder_number, post.play_url)
    if post.ladder_number:
        return 'Лесенка №{}\n{}'.format(post.ladder_number, post.play_url)
    return post.caption or ''


def _filename(post: SocialQueuePost) -> str:
    if post.source == SocialQueuePost.SOURCE_WORD_SALAD and post.ladder_number:
        return 'salad-{}.png'.format(post.ladder_number)
    if post.ladder_number:
        return 'ladder-{}.png'.format(post.ladder_number)
    return 'social-{}.png'.format(post.pk or 'draft')


def queue_failed_network_retries(
    post: SocialQueuePost,
    *,
    now: datetime | None = None,
) -> SocialQueuePost:
    """Queue bounded retries for failed networks without repeating successful ones."""
    retry_at = (now or timezone.now()) + SOCIAL_QUEUE_RETRY_DELAY
    updates = {}
    for network in ('telegram', 'twitter', 'instagram'):
        if getattr(post, '{}_status'.format(network)) != SocialQueuePost.STATUS_FAILED:
            continue
        if getattr(post, '{}_attempts'.format(network)) >= SOCIAL_QUEUE_MAX_ATTEMPTS:
            continue
        updates['{}_status'.format(network)] = SocialQueuePost.STATUS_QUEUED
        updates['{}_queued_for'.format(network)] = retry_at
    if updates:
        SocialQueuePost.objects.filter(pk=post.pk).update(**updates)
        for field, value in updates.items():
            setattr(post, field, value)
    return post


def publish_telegram(
    post: SocialQueuePost,
    *,
    immediate: bool = False,
    schedule_at: datetime | None = None,
    force: bool = False,
    claim_token: str | None = None,
) -> SocialQueuePost:
    """Post/schedule photo to the Telegram channel. Updates telegram_* fields."""
    post._telegram_completion_applied = False
    if post.telegram_ok and post.telegram_external_id and not force:
        return post

    if claim_token is None:
        claim_token = claim_telegram_post(post.pk, force=force)
        if claim_token is None:
            post.refresh_from_db()
            return post
    elif not telegram_claim_is_owned(post.pk, claim_token):
        post.refresh_from_db()
        return post

    post.refresh_from_db()

    if not (telegram_user_configured() and telegram_channel_configured()):
        post._telegram_completion_applied = complete_telegram_publish(
            post.pk,
            claim_token,
            status=SocialQueuePost.STATUS_SKIPPED,
            error='Telegram channel / user session not configured',
        )
        post.refresh_from_db()
        return post

    data = post.image_bytes()
    if not data:
        post._telegram_completion_applied = complete_telegram_publish(
            post.pk,
            claim_token,
            status=SocialQueuePost.STATUS_FAILED,
            error='No image on post',
        )
        post.refresh_from_db()
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
    if not _begin_telegram_attempt(post.pk, claim_token):
        post.refresh_from_db()
        return post
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
        post._telegram_completion_applied = complete_telegram_publish(
            post.pk,
            claim_token,
            status=SocialQueuePost.STATUS_FAILED,
            error=str(exc)[:500],
            scheduled_for=use_schedule,
        )
        post.refresh_from_db()
        return post

    if immediate or use_schedule is None:
        status = SocialQueuePost.STATUS_SENT
    else:
        status = SocialQueuePost.STATUS_SCHEDULED
    post._telegram_completion_applied = complete_telegram_publish(
        post.pk,
        claim_token,
        status=status,
        external_id=str(result.get('message_id') or ''),
        telegram_at=timezone.now(),
        scheduled_for=use_schedule,
    )
    post.refresh_from_db()
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
        post.telegram_claimed_at = None
        post.telegram_claim_token = ''
        post.save(update_fields=[
            'telegram_status', 'telegram_queued_for', 'telegram_error',
            'telegram_claimed_at', 'telegram_claim_token', 'updated_at',
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


def _publish_one_queued(
    network: str,
    pk: int,
    *,
    telegram_claim_token: str | None = None,
) -> bool:
    """Publish a single queued post to one network. Returns True on success.

    Each publish re-fetches its own post instance and only writes that network's
    columns, so concurrent work on the same post across networks does not clobber.
    """
    post = SocialQueuePost.objects.filter(pk=pk).first()
    if post is None:
        return False
    if network == 'telegram':
        publish_telegram(
            post,
            immediate=True,
            force=False,
            claim_token=telegram_claim_token,
        )
    elif network == 'twitter':
        publish_twitter(post, force=False)
    elif network == 'instagram':
        publish_instagram(post, force=False)
    else:
        raise ValueError('Unknown network: {}'.format(network))
    queue_failed_network_retries(post)
    return True


def _publish_one_queued_worker(
    network: str,
    pk: int,
    telegram_claim_token: str | None = None,
) -> bool:
    """Thread-pool entrypoint: closes the thread-local DB connection on exit.

    Django only auto-closes connections opened on the request thread, so pool
    threads would otherwise leak a connection per task.
    """
    try:
        return _publish_one_queued(
            network,
            pk,
            telegram_claim_token=telegram_claim_token,
        )
    finally:
        connection.close()


def process_social_queue_tick(now: datetime | None = None) -> dict[str, Any]:
    """Publish networks whose internal queued_for time has arrived.

    Each post/network publish is an independent, I/O-bound external call, so we
    fan them out over a bounded thread pool instead of publishing serially. The
    worker count is capped by SOCIAL_QUEUE_MAX_WORKERS (default 8). SQLite cannot
    handle concurrent writers, so we fall back to inline serial publishing there
    (covers the test DB); the production database parallelizes.
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

    telegram_stale_before = now - QUEUE_CLAIM_TIMEOUT
    telegram_pks = list(
        SocialQueuePost.objects.filter(
            telegram_external_id='',
            telegram_queued_for__lte=now,
        ).filter(
            Q(telegram_status=SocialQueuePost.STATUS_QUEUED)
            | (
                Q(telegram_status=SocialQueuePost.STATUS_PUBLISHING)
                & (
                    Q(telegram_claimed_at__isnull=True)
                    | Q(telegram_claimed_at__lt=telegram_stale_before)
                )
            )
        ).values_list('pk', flat=True)[:50]
    )

    tasks: list[tuple[str, int]] = []
    for pk in telegram_pks:
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
                claim = _claim_queued_post(network, pk, now)
                if not claim:
                    continue
                if _publish_one_queued(
                    network,
                    pk,
                    telegram_claim_token=claim if network == 'telegram' else None,
                ):
                    stats[network] += 1
            except Exception:
                logger.exception('Social queue tick %s failed pk=%s', network, pk)
                stats['errors'] += 1
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {}
            for network, pk in tasks:
                claim = _claim_queued_post(network, pk, now)
                if not claim:
                    continue
                future = executor.submit(
                    _publish_one_queued_worker,
                    network,
                    pk,
                    claim if network == 'telegram' else None,
                )
                future_to_task[future] = (network, pk)
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

    data = post.social_image_bytes()
    if not data:
        post.twitter_status = SocialQueuePost.STATUS_FAILED
        post.twitter_error = 'No image on post'
        post.save(update_fields=['twitter_status', 'twitter_error', 'updated_at'])
        return post

    post.twitter_attempts += 1
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
            'twitter_status', 'twitter_external_id', 'twitter_error', 'twitter_at',
            'twitter_attempts', 'updated_at',
        ])
    except Exception as exc:
        logger.exception('Twitter publish failed for social post pk=%s', post.pk)
        post.twitter_status = SocialQueuePost.STATUS_FAILED
        post.twitter_error = str(exc)[:500]
        post.save(update_fields=[
            'twitter_status', 'twitter_error', 'twitter_attempts', 'updated_at',
        ])
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

    if not (post.social_image or post.image):
        post.instagram_status = SocialQueuePost.STATUS_FAILED
        post.instagram_error = 'No image on post'
        post.save(update_fields=['instagram_status', 'instagram_error', 'updated_at'])
        return post

    image_url = settings.SITE_BASE_URL + reverse(
        'social_queue_instagram_jpg', args=[post.pk]
    )
    post.instagram_attempts += 1
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
            'instagram_attempts',
            'updated_at',
        ])
    except Exception as exc:
        logger.exception('Instagram publish failed for social post pk=%s', post.pk)
        post.instagram_status = SocialQueuePost.STATUS_FAILED
        post.instagram_error = str(exc)[:500]
        post.save(update_fields=[
            'instagram_status', 'instagram_error', 'instagram_attempts', 'updated_at',
        ])
    return post
