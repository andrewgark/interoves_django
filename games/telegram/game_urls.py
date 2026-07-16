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
    return admin_url(game_play_path(game))


def game_play_path(game) -> str:
    """Relative play URL for a game hub page (project-scoped when needed)."""
    project = getattr(game, 'project', None)
    if project is not None and not project.is_main():
        return '/{}/games/{}/'.format(project.id, game.id)
    return '/games/{}/'.format(game.id)


def task_group_play_path(game, task_group_number) -> str:
    return '{}{}/'.format(game_play_path(game), task_group_number)


def task_play_url(game, task) -> str:
    """
    Absolute URL of the task on the site (task group page + #new-task-<id>).
    Falls back to the game hub if the task is not linked into the game.
    """
    from games.models import GameTaskGroup

    if task is None:
        return game_site_url(game)
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


def game_answers_url(game) -> str:
    if game.answers_url:
        return game.answers_url
    return (game.tags or {}).get('answers_url') or ''


def game_standings_url(game) -> str:
    if game.standings_url:
        return game.standings_url
    return admin_url('/games/{}/results/'.format(game.id))
