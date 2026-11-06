"""Static outbound links for main and project-scoped hub pages.

Telegram chat invites are ``members_only``: anonymous visitors (and the bots
scraping the hub) do not see them; signed-in players do.
"""

MAIN_DESYATOCHKI_PARTICIPANTS_CHAT_URL = 'https://t.me/+rhsbkEuU4-ExOWEy'

MAIN_COMMUNITY_LINKS = [
    {'kind': 'telegram', 'title': 'Телеграм-канал', 'href': 'https://t.me/interoves'},
    {'kind': 'twitter', 'title': 'X (Twitter)', 'href': 'https://x.com/interoves'},
    {'kind': 'instagram', 'title': 'Instagram', 'href': 'https://www.instagram.com/interoveslocumpraesta/'},
    {'kind': 'telegram', 'title': 'Чат участников', 'href': MAIN_DESYATOCHKI_PARTICIPANTS_CHAT_URL, 'members_only': True},
    {
        'kind': 'telegram',
        'title': 'Чат решающих PuzzleHunts',
        'href': 'https://t.me/+GPR22w8MdLEyNzIy',
        'members_only': True,
    },
    {'kind': 'telegram', 'title': 'Разработчик: Андрей', 'href': 'https://t.me/andrewgark'},
]

MAIN_INTERESTING_LINKS = [
    {
        'kind': 'nutrimatic',
        'title': 'Nutrimatic',
        'note': 'поиск слов по маске',
        'href': '/nutrimatic-ru/',
    },
    {
        'kind': 'eurovision',
        'title': 'Буклеты к Евровидению',
        'note': 'красивые',
        'href': '/eurovision_booklet/',
    },
    {'kind': 'vpn', 'title': 'VPN от наших друзей', 'href': '/vpn/'},
]

GLOWBYTE_PARTICIPANTS_CHAT_URL = 'https://t.me/joinchat/RUpU9KKhgLI4NDQy'

GLOWBYTE_COMMUNITY_LINKS = [
    {'kind': 'telegram', 'title': 'Чат участников', 'href': GLOWBYTE_PARTICIPANTS_CHAT_URL, 'members_only': True},
]


def visible_community_links(links: list, user) -> list:
    """Drop members-only links (chat invites) for anonymous visitors."""
    if getattr(user, 'is_authenticated', False):
        return list(links)
    return [link for link in links if not link.get('members_only')]


def members_chat_url(url: str, user):
    """Chat invite URL for signed-in users, ``None`` for anonymous visitors."""
    return url if getattr(user, 'is_authenticated', False) else None


def project_hub_community_links(project_id: str, user) -> list:
    if project_id == 'glowbyte':
        return visible_community_links(GLOWBYTE_COMMUNITY_LINKS, user)
    return []


def project_desyatochki_rules_chat_url(project_id: str, user):
    if project_id == 'glowbyte':
        return members_chat_url(GLOWBYTE_PARTICIPANTS_CHAT_URL, user)
    return members_chat_url(MAIN_DESYATOCHKI_PARTICIPANTS_CHAT_URL, user)
