"""Static outbound links for main and project-scoped hub pages."""

MAIN_DESYATOCHKI_PARTICIPANTS_CHAT_URL = 'https://t.me/+rhsbkEuU4-ExOWEy'

MAIN_COMMUNITY_LINKS = [
    {'kind': 'telegram', 'title': 'Телеграм-канал', 'href': 'https://t.me/interoves'},
    {'kind': 'twitter', 'title': 'X (Twitter)', 'href': 'https://x.com/interoves'},
    {'kind': 'instagram', 'title': 'Instagram', 'href': 'https://www.instagram.com/interoveslocumpraesta/'},
    {'kind': 'telegram', 'title': 'Чат участников', 'href': MAIN_DESYATOCHKI_PARTICIPANTS_CHAT_URL},
    {'kind': 'telegram', 'title': 'Чат решающих PuzzleHunts', 'href': 'https://t.me/+GPR22w8MdLEyNzIy'},
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
    {'kind': 'telegram', 'title': 'Чат участников', 'href': GLOWBYTE_PARTICIPANTS_CHAT_URL},
]


def project_hub_community_links(project_id: str) -> list:
    if project_id == 'glowbyte':
        return list(GLOWBYTE_COMMUNITY_LINKS)
    return []


def project_desyatochki_rules_chat_url(project_id: str) -> str:
    if project_id == 'glowbyte':
        return GLOWBYTE_PARTICIPANTS_CHAT_URL
    return MAIN_DESYATOCHKI_PARTICIPANTS_CHAT_URL
