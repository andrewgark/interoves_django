"""Permanent redirects from the retired old interface (/old/… and old deep links).

Where the new UI has the same page, the link moves there with its query
string; everything else under /old/ answers 410 Gone.
"""

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect

from games.models import Game
from games.sections.project_navigation import project_base


def _redirect(request, path):
    query = request.META.get('QUERY_STRING')
    if query:
        path = '{}?{}'.format(path, query)
    return redirect(path, permanent=True)


def _game_path(game_id, suffix=''):
    game = get_object_or_404(Game, id=game_id)
    return '{}/games/{}/{}'.format(project_base(game.project_id), game.id, suffix)


def project_home(request, project_id='main'):
    return _redirect(request, project_base(project_id) + '/')


def game(request, game_id, task_group=None, task=None):
    """Old game page and its task-group / single-task deep links."""
    suffix = '{}/'.format(task_group) if task_group is not None else ''
    return _redirect(request, _game_path(game_id, suffix))


def results(request, game_id, mode='general'):
    suffix = 'tournament-results/' if mode == 'tournament' else 'results/'
    return _redirect(request, _game_path(game_id, suffix))


def tournament_results(request, game_id):
    return results(request, game_id, mode='tournament')


def to_path(path):
    """Same page outside /old/ (team pages, puzzle pages)."""
    def view(request, **kwargs):
        return _redirect(request, path.format(**kwargs))
    return view


def gone(request, *args, **kwargs):
    return HttpResponse(status=410)
