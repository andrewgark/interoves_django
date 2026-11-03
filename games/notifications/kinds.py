"""Notification groups (what the player switches) and kinds (what code sends)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

GROUP_FRIENDS = 'friends'
GROUP_SUPPORT = 'support'
GROUP_OFFERS = 'offers'
GROUP_GAMES = 'games'
GROUP_CLUB = 'club'


@dataclass(frozen=True)
class NotificationGroup:
    key: str
    title: str
    telegram_default: bool
    # Mandatory channels cannot be switched off by the player.
    site_mandatory: bool = False
    telegram_mandatory: bool = False


GROUPS: dict[str, NotificationGroup] = {
    group.key: group for group in (
        NotificationGroup(GROUP_FRIENDS, 'Друзья и команда', telegram_default=True),
        NotificationGroup(GROUP_SUPPORT, 'Поддержка', telegram_default=True, site_mandatory=True),
        NotificationGroup(GROUP_OFFERS, 'Мои задания', telegram_default=True),
        NotificationGroup(GROUP_GAMES, 'Игры', telegram_default=True),
        NotificationGroup(
            GROUP_CLUB, 'Клуб и оплата', telegram_default=True,
            site_mandatory=True, telegram_mandatory=True,
        ),
    )
}


def _always_valid(_notification) -> bool:
    return True


@dataclass(frozen=True)
class NotificationKind:
    key: str
    group: str
    # data -> the part of group_key after the kind; equal keys fold together.
    group_key: Callable[[dict], str]
    # notification -> {'title', 'body', 'url'} or None to hide it.
    render: Callable
    # notification -> {'text', 'reply_markup'} or None to skip Telegram.
    render_telegram: Callable
    # Re-read before showing and before sending: is it still true?
    is_valid: Callable = _always_valid
    # Wait before Telegram so a player who is on the site reads it there.
    telegram_delay: timedelta = timedelta(minutes=2)
    # Urgent kinds ignore quiet hours.
    urgent: bool = False
    # Folding a new event into an unread row sends Telegram again.
    telegram_on_merge: bool = False
    # No second Telegram copy for the same group_key within this window.
    telegram_cooldown: timedelta | None = None
    # Opening any of these url names marks the notification read.
    mark_read_on: tuple[str, ...] = ()

    def full_group_key(self, data: dict) -> str:
        return '{}:{}'.format(self.key, self.group_key(data))


_REGISTRY: dict[str, NotificationKind] = {}


def register(kind: NotificationKind) -> NotificationKind:
    if kind.group not in GROUPS:
        raise ValueError('unknown notification group: {}'.format(kind.group))
    if kind.key in _REGISTRY:
        raise ValueError('duplicate notification kind: {}'.format(kind.key))
    _REGISTRY[kind.key] = kind
    return kind


def _load_catalog():
    # The catalog imports domain modules; load it lazily to keep imports acyclic.
    from games.notifications import catalog  # noqa: F401


def get_kind(key: str) -> NotificationKind | None:
    _load_catalog()
    return _REGISTRY.get(key)


def all_kinds() -> list[NotificationKind]:
    _load_catalog()
    return list(_REGISTRY.values())
