"""Worker side of personal Telegram notices.

New notices come from ``games.notifications`` (payload kind
``notification``). The bare ``friend_request`` payload predates it and is
still handled so messages queued before the deploy are not lost.
"""

from __future__ import annotations

import html
import logging

from django.urls import reverse

from games.ops.telegram.links import site_base_url

logger = logging.getLogger('application')

FRIEND_REQUEST = 'friend_request'


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
    if kind == 'notification':
        from games.notifications.telegram import run_worker_notice

        result = run_worker_notice(payload)
    elif kind == FRIEND_REQUEST:
        result = _run_friend_request(payload)
    else:
        result = {'sent': False, 'reason': 'unknown_kind'}
    logger.info('event=telegram_user_notice kind=%s sent=%s reason=%s', kind, result['sent'], result['reason'])
    return result
