from urllib.parse import urljoin

from django.conf import settings


def site_base_url() -> str:
    return getattr(settings, 'SITE_BASE_URL', 'https://interoves.com').rstrip('/')


def admin_url(path: str) -> str:
    return urljoin(site_base_url() + '/', path.lstrip('/'))


def game_site_url(game) -> str:
    custom = (game.tags or {}).get('site_url')
    if custom:
        return str(custom).rstrip('/')
    return admin_url('/games/{}/'.format(game.id))


def game_conditions_url(game) -> str:
    """Google Doc with game conditions / tasks booklet."""
    if game.game_url and str(game.game_url).startswith('http'):
        return game.game_url
    custom = (game.tags or {}).get('conditions_url')
    if custom:
        return str(custom)
    return ''


def game_answers_url(game) -> str:
    if game.answers_url:
        return game.answers_url
    return (game.tags or {}).get('answers_url') or ''


def game_standings_url(game) -> str:
    if game.standings_url:
        return game.standings_url
    return admin_url('/games/{}/results/'.format(game.id))
