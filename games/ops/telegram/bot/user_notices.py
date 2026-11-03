"""Personal Telegram notices to players (via the integrations worker).

The web process only enqueues; the worker re-reads the database before
sending, so a request that was cancelled or answered in the meantime is not
announced.
"""

from __future__ import annotations

import html
import logging

from django.core.cache import cache
from django.urls import reverse

from games.ops.telegram.bot.alerts import enqueue_integrations_message
from games.ops.telegram.links import site_base_url
from games.ops.workers.messages import TELEGRAM_USER_NOTICE

logger = logging.getLogger('application')

FRIEND_REQUEST = 'friend_request'
# One notice per pair: cancelling and re-sending a request must not turn the
# bot into a way to ping somebody repeatedly.
FRIEND_REQUEST_NOTICE_TTL = 7 * 24 * 3600


def _friend_request_cache_key(from_user_id, to_user_id):
    return 'telegram:user_notice:friend_request:{}:{}'.format(from_user_id, to_user_id)


def publish_friend_request_notice(*, from_user_id: int, to_user_id: int) -> bool:
    from games.models import Profile

    if not Profile.objects.filter(
        user_id=to_user_id, telegram_verified=True, telegram_user_id__isnull=False,
    ).exists():
        return False
    key = _friend_request_cache_key(from_user_id, to_user_id)
    if not cache.add(key, 1, FRIEND_REQUEST_NOTICE_TTL):
        return False
    enqueued = False
    try:
        enqueued = enqueue_integrations_message(
            message_type=TELEGRAM_USER_NOTICE,
            payload={'kind': FRIEND_REQUEST, 'from_user_id': int(from_user_id), 'to_user_id': int(to_user_id)},
            dedupe_key='telegram.user_notice:friend_request:{}:{}'.format(from_user_id, to_user_id),
            label='user_notice_friend_request',
        )
    finally:
        if not enqueued:
            # Nothing was sent: let the next request for this pair try again.
            cache.delete(key)
    return enqueued


def format_friend_request_notice(sender_profile) -> str:
    name = html.escape(sender_profile.display_name or 'Игрок', quote=False)
    base = site_base_url()
    page = base + reverse('new_player_profile', kwargs={'public_id': sender_profile.public_id})
    friends_page = base + reverse('new_friends')
    return '\n'.join([
        '🤝 <b>{}</b> хочет добавить вас в друзья на Интеровесе.'.format(name),
        '',
        'Друзей видно отдельным фильтром в таблицах результатов.',
        '',
        '<a href="{}">Кто это</a> · <a href="{}">Ответить на заявку</a>'.format(page, friends_page),
    ])


def _run_friend_request(payload) -> dict:
    from django.contrib.auth.models import User

    from games.community.friends import RELATION_INCOMING, relation
    from games.models import Profile
    from games.ops.telegram.bot.notify import send_user_telegram_message

    recipient = User.objects.filter(pk=payload.get('to_user_id'), is_active=True).select_related('profile').first()
    sender = Profile.objects.filter(
        user_id=payload.get('from_user_id'), is_hidden=False, user__is_active=True,
    ).first()
    if recipient is None or sender is None or not getattr(recipient, 'profile', None):
        return {'sent': False, 'reason': 'missing'}
    if relation(recipient, sender.user_id) != RELATION_INCOMING:
        return {'sent': False, 'reason': 'not_pending'}
    if not recipient.profile.telegram_verified:
        return {'sent': False, 'reason': 'no_telegram'}
    sent = send_user_telegram_message(recipient, format_friend_request_notice(sender))
    return {'sent': bool(sent), 'reason': '' if sent else 'delivery_failed'}


def run_user_notice_live(*, payload: dict) -> dict:
    kind = str(payload.get('kind') or '')
    if kind == FRIEND_REQUEST:
        result = _run_friend_request(payload)
    else:
        result = {'sent': False, 'reason': 'unknown_kind'}
    logger.info('event=telegram_user_notice kind=%s sent=%s reason=%s', kind, result['sent'], result['reason'])
    return result
