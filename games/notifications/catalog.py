"""Every notification kind: how it folds, renders and stays valid.

Rendering happens on read, so each kind re-reads its objects by id.
"""

from __future__ import annotations

from datetime import timedelta

from django.urls import reverse

from games.notifications.kinds import (
    GROUP_CLUB,
    GROUP_FRIENDS,
    GROUP_SUPPORT,
    NotificationKind,
    register,
)

# ---- friends ---------------------------------------------------------------


def _friend_sender(notification):
    from games.models import Profile

    return Profile.objects.filter(
        user_id=notification.actor_id, is_hidden=False, user__is_active=True,
    ).first()


def _friend_request_valid(notification) -> bool:
    from games.community.friends import RELATION_INCOMING, relation

    if notification.actor_id is None or _friend_sender(notification) is None:
        return False
    return relation(notification.user, notification.actor_id) == RELATION_INCOMING


def _friend_request_render(notification):
    sender = _friend_sender(notification)
    if sender is None:
        return None
    return {
        'title': '{} хочет добавить вас в друзья'.format(sender.display_name or 'Игрок'),
        'body': '',
        'url': reverse('new_friends'),
        'actor_url': reverse('new_player_profile', kwargs={'public_id': sender.public_id}),
    }


def _friend_request_telegram(notification):
    from games.ops.telegram.bot.user_notices import format_friend_request_notice

    sender = _friend_sender(notification)
    if sender is None:
        return None
    return {'text': format_friend_request_notice(sender)}


FRIEND_REQUEST = register(NotificationKind(
    key='friend.request',
    group=GROUP_FRIENDS,
    group_key=lambda data: str(data['from_user_id']),
    render=_friend_request_render,
    render_telegram=_friend_request_telegram,
    is_valid=_friend_request_valid,
    # Cancelling and re-sending must not turn the bot into a way to ping someone.
    telegram_cooldown=timedelta(days=7),
    mark_read_on=('new_friends', 'ui_friends'),
))

# ---- support ---------------------------------------------------------------


def _support_message(notification):
    from games.models import BugReportMessage

    return (
        BugReportMessage.objects
        .select_related('report', 'report__task', 'report__task__task_group', 'report__game', 'report__game__project')
        .filter(pk=notification.payload.get('message_id'))
        .first()
    )


def _support_reply_render(notification):
    from games.site.pages.profile.feedback import profile_reports_path

    message = _support_message(notification)
    if message is None:
        return None
    text = message.text or ''
    return {
        'title': 'Ответ по вашему сообщению о проблеме',
        'body': text if len(text) <= 200 else text[:199].rstrip() + '…',
        'url': profile_reports_path(report_id=message.report_id),
    }


def _support_reply_telegram(notification):
    from games.ops.telegram.bot.notify import format_bug_report_admin_reply_message

    message = _support_message(notification)
    if message is None:
        return None
    return {'text': format_bug_report_admin_reply_message(message)}


SUPPORT_REPLY = register(NotificationKind(
    key='support.reply',
    group=GROUP_SUPPORT,
    group_key=lambda data: str(data['report_id']),
    render=_support_reply_render,
    render_telegram=_support_reply_telegram,
    is_valid=lambda notification: _support_message(notification) is not None,
    telegram_delay=timedelta(0),
    # Each new answer in the thread is news.
    telegram_on_merge=True,
))

# ---- club ------------------------------------------------------------------


def _club_active_render(_notification):
    return {
        'title': 'Клубная подписка Inter Oves активна',
        'body': 'Приглашение в закрытый чат клуба отправили вам в Telegram.',
        'url': reverse('new_subscription'),
    }


def _club_active_telegram(_notification):
    from games.ops.telegram.transport.config import club_invite_url

    invite_url = club_invite_url()
    if not invite_url:
        return None
    return {
        'text': (
            '✅ <b>Клубная подписка Inter Oves активна!</b>\n\n'
            'Вступите в закрытый чат клуба Inter Oves Elite:\n{}'.format(invite_url)
        ),
    }


CLUB_ACTIVE = register(NotificationKind(
    key='club.active',
    group=GROUP_CLUB,
    group_key=lambda data: str(data['subscription_id']),
    render=_club_active_render,
    render_telegram=_club_active_telegram,
    telegram_delay=timedelta(0),
    urgent=True,
    mark_read_on=('new_subscription', 'ui_subscription'),
))
