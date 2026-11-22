import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from django.conf import settings


def site_base_url() -> str:
    return getattr(settings, 'SITE_BASE_URL', 'https://interoves.com').rstrip('/')


def admin_url(path: str) -> str:
    return urljoin(site_base_url() + '/', path.lstrip('/'))


def game_site_url(game) -> str:
    custom = (game.tags or {}).get('site_url')
    if custom:
        return str(custom).rstrip('/')
    return admin_url(game_play_path(game))


def game_play_path(game) -> str:
    """Relative play URL for a game hub page (project-scoped when needed)."""
    from games.sections.paths import is_root_section_game, section_hub_path

    if is_root_section_game(getattr(game, 'id', None)):
        return section_hub_path(game.id)
    project_id = getattr(game, 'project_id', None)
    if project_id is None:
        project = getattr(game, 'project', None)
        project_id = getattr(project, 'id', None)
    # "sections" is a DB project for hub tiles, not a URL prefix like /glowbyte/.
    if project_id and project_id not in ('main', 'sections'):
        return '/{}/games/{}/'.format(project_id, game.id)
    return '/games/{}/'.format(game.id)


def task_group_play_path(game, task_group_number) -> str:
    return '{}{}/'.format(game_play_path(game), task_group_number)


def _offer_share_path(task, game=None) -> str:
    """Public /<game>/<hash>/ for a custom offer or a schedule slot."""
    if task is None or not getattr(task, 'task_group_id', None):
        return ''
    from games.models import AlphabettyOffer, GameTaskGroup, LadderOffer, WordSaladOffer
    from games.sections.paths import section_play_path

    for model in (WordSaladOffer, LadderOffer, AlphabettyOffer):
        offer = (
            model.objects.filter(task_group_id=task.task_group_id)
            .exclude(share_hash='')
            .first()
        )
        if offer is None:
            continue
        path = offer.play_url()
        if path:
            return path
    game_id = getattr(game, 'id', None)
    links = GameTaskGroup.objects.filter(task_group_id=task.task_group_id).exclude(share_hash='')
    if game_id:
        links = links.filter(game_id=game_id)
    link = links.only('game_id', 'share_hash').first()
    if link is None or not link.share_hash:
        return ''
    return section_play_path(link.game_id, link.share_hash)


def task_play_url(game, task) -> str:
    """
    Absolute URL of the task on the site (task group page + #new-task-<id>).
    A custom offer's share link wins over the numeric daily slot.
    Falls back to the game hub if the task is not linked into the game.
    """
    from games.models import GameTaskGroup

    if task is None:
        return game_site_url(game)
    share = _offer_share_path(task, game)
    if share:
        return admin_url('{}#new-task-{}'.format(share, task.pk))
    link = None
    if task.task_group_id:
        link = (
            GameTaskGroup.objects
            .filter(game_id=game.id, task_group_id=task.task_group_id)
            .only('number')
            .first()
        )
    if link is None:
        return game_site_url(game)
    path = task_group_play_path(game, link.number)
    return admin_url('{}#new-task-{}'.format(path, task.pk))


SECTION_SUPPORT_PATHS = {
    'ladder': '/support/ladders/',
    'alphabetty': '/support/alphabetty/',
    'salad': '/support/salad/',
    'censorly': '/support/censorly/',
}


def support_url(path: str = '/support/') -> str:
    """Absolute link into the /support console, the admin's working tool."""
    return admin_url(path)


def user_support_url(user) -> str:
    return support_url('/support/actor/user/{}/'.format(user.pk))


def team_support_url(team) -> str:
    from urllib.parse import quote

    return support_url('/support/actor/team/{}/'.format(quote(str(team.name), safe='')))


def game_support_url(game) -> str:
    game_id = getattr(game, 'id', game)
    section = SECTION_SUPPORT_PATHS.get(str(game_id))
    if section:
        return support_url(section)
    return support_url('/support/game/{}/'.format(game_id))


def task_support_url(task, game=None) -> str:
    """Where the admin edits or previews a task: the daily section or the task group preview."""
    game_id = getattr(game, 'id', None) or getattr(task, 'game_id', None)
    if not game_id and getattr(task, 'task_group_id', None):
        from games.models import GameTaskGroup

        game_id = (
            GameTaskGroup.objects.filter(task_group_id=task.task_group_id)
            .values_list('game_id', flat=True).first()
        )
    section = SECTION_SUPPORT_PATHS.get(str(game_id or ''))
    if section:
        return support_url(section)
    if game_id and getattr(task, 'task_group_id', None):
        from games.models import GameTaskGroup

        number = (
            GameTaskGroup.objects.filter(game_id=game_id, task_group_id=task.task_group_id)
            .values_list('number', flat=True).first()
        )
        if number:
            return support_url('/support/preview/games/{}/{}/'.format(game_id, number))
    if game_id:
        return support_url('/support/game/{}/'.format(game_id))
    return support_url()


def bug_report_support_url(report) -> str:
    return support_url('/support/reports/{}/'.format(getattr(report, 'pk', report)))


def task_admin_url(task) -> str:
    return admin_url('/admin/games/task/{}/change/'.format(task.pk))


def task_group_admin_url(task_group) -> str:
    return admin_url('/admin/games/taskgroup/{}/change/'.format(task_group.pk))


def game_admin_url(game) -> str:
    return admin_url('/admin/games/game/{}/change/'.format(game.id))


def game_conditions_url(game) -> str:
    """Google Doc with game conditions / tasks booklet."""
    if game.game_url and str(game.game_url).startswith('http'):
        return game.game_url
    custom = (game.tags or {}).get('conditions_url')
    if custom:
        return str(custom)
    return ''


def game_conditions_copy_url(game) -> str:
    """Google Docs URL that opens a copy prompt for the game conditions."""
    conditions_url = game_conditions_url(game)
    if not conditions_url:
        return ''
    try:
        parsed = urlsplit(conditions_url)
    except ValueError:
        return ''
    if (parsed.hostname or '').lower() != 'docs.google.com':
        return ''

    path = parsed.path or ''
    if '/copy' in path:
        copy_path = path
    elif re.search(r'/edit/?$', path):
        copy_path = re.sub(r'/edit/?$', '/copy', path)
    elif re.match(r'^/(document|spreadsheets|presentation)/', path):
        copy_path = path.rstrip('/') + '/copy'
    else:
        return ''
    return urlunsplit(parsed._replace(path=copy_path))


def game_answers_url(game) -> str:
    if game.answers_url:
        return game.answers_url
    return (game.tags or {}).get('answers_url') or ''


def game_standings_url(game) -> str:
    if game.standings_url:
        return game.standings_url
    return admin_url('{}results/'.format(game_play_path(game)))


def game_tournament_results_url(game) -> str:
    return admin_url('{}tournament-results/'.format(game_play_path(game)))
