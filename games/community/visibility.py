"""Who sees what on public player pages.

A public page never shows contacts (Telegram, VK, e-mail). Players control
two switches on their own profile: ``page_hidden`` and ``avatar_hidden``.
The admin ``is_hidden`` flag only keeps the player out of results tables;
their page, friendships and team rosters work as for anyone else.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from django.urls import reverse

from games.models import Profile

# Others only see photos served by the login providers themselves. An
# arbitrary URL would let a player log the IP of everyone who opens their page
# (or show anything at all); the owner still sees whatever they set.
TRUSTED_AVATAR_HOST_SUFFIXES = (
    'userapi.com', 'vk.com', 'vk.me', 'vkuserphoto.ru',   # VK
    'googleusercontent.com', 'ggpht.com',                  # Google
    'yandex.net',                                          # Яндекс
    't.me', 'telesco.pe', 'telegram.org',                  # Telegram
)

PAGE_FULL = 'full'
PAGE_HIDDEN = 'hidden'
PAGE_MISSING = 'missing'


def is_owner(viewer, profile):
    return bool(getattr(viewer, 'is_authenticated', False) and viewer.pk == profile.user_id)


def page_state(viewer, profile):
    """Return ``full``, ``hidden`` (stub with the name only) or ``missing`` (404)."""
    if profile is None:
        return PAGE_MISSING
    if is_owner(viewer, profile):
        return PAGE_FULL
    # Deactivated accounts (e.g. the losing side of an account merge) must
    # not linger as public duplicates of a player.
    if not profile.user.is_active:
        return PAGE_MISSING
    if profile.page_hidden:
        # Confirmed friends still see the page: hiding it is about strangers.
        from games.community.friends import are_friends

        if are_friends(getattr(viewer, 'pk', None), profile.user_id):
            return PAGE_FULL
        return PAGE_HIDDEN
    return PAGE_FULL


def public_avatar_url(profile, viewer=None, *, page_visible=False):
    """Avatar URL as other players see it; the owner still sees their photo.

    A hidden page hides the photo too (the player asked to show only a
    name), unless this viewer may see the page anyway (``page_visible``).
    """
    if not profile.avatar_url:
        return ''
    if viewer is not None and is_owner(viewer, profile):
        return profile.avatar_url
    if profile.avatar_hidden or (profile.page_hidden and not page_visible):
        return ''
    return profile.avatar_url if is_trusted_avatar_url(profile.avatar_url) else ''


def is_trusted_avatar_url(url):
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return False
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or not host:
        return False
    return any(host == suffix or host.endswith('.' + suffix) for suffix in TRUSTED_AVATAR_HOST_SUFFIXES)


def initials(profile):
    parts = [(profile.first_name or '').strip(), (profile.last_name or '').strip()]
    return ''.join(part[0] for part in parts if part).upper() or '?'


@dataclass(frozen=True)
class PublicIdentity:
    name: str
    initials: str
    avatar_url: str
    url: str


def public_identity(profile, viewer=None, *, page_visible=False):
    return PublicIdentity(
        name=profile.display_name or 'Игрок',
        initials=initials(profile),
        avatar_url=public_avatar_url(profile, viewer, page_visible=page_visible),
        url=player_url(profile.public_id),
    )


def player_url(public_id):
    return reverse('new_player_profile', kwargs={'public_id': public_id}) if public_id else ''


def player_urls_for_users(user_ids, viewer=None):
    """``{user_id: url}`` for pages this viewer may open; one or two queries.

    Players with a hidden page get no link, so results tables do not
    advertise a page that only shows a stub, except to the player themself
    and to their friends, who see the full page.
    """
    ids = {int(uid) for uid in user_ids if uid is not None}
    if not ids:
        return {}
    rows = Profile.objects.filter(
        user_id__in=ids, user__is_active=True,
    ).exclude(public_id=None).values_list('user_id', 'public_id', 'page_hidden')
    hidden = {user_id for user_id, _public_id, page_hidden in rows if page_hidden}
    allowed = set()
    viewer_id = viewer.pk if getattr(viewer, 'is_authenticated', False) else None
    if hidden and viewer_id is not None:
        from games.community.friends import friend_user_ids

        allowed = (friend_user_ids(viewer_id) | {viewer_id}) & hidden
    return {
        user_id: player_url(public_id)
        for user_id, public_id, page_hidden in rows
        if not page_hidden or user_id in allowed
    }
