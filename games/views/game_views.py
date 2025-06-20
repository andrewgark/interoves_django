import datetime
import json
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render, get_object_or_404
from django.views import defaults
from django.utils import timezone
from games.access import game_has_started
from games.exception import NoGameAccessException
from games.models import Game, Team, Attempt, ImageManager, AudioManager
from games.views.render_task import get_task_to_attempts_info, get_all_text_with_forms_to_html
from games.views.team_views import get_team_to_play_page
from games.views.util import has_profile, has_team
from games.views.results_views import results_page


def game_page(request, game_id, task_group=None, task=None):
    game = get_object_or_404(Game, id=game_id)
    if not has_profile(request.user) or not request.user.profile.team_on:
        return get_team_to_play_page(request, game)
    team = None
    if has_team(request.user):
        team = request.user.profile.team_on
    if not game_has_started(game):
        return HttpResponse(
            status=405,
            content='Игра еще не началась, дождитесь {}'.format(game.start_time.strftime('%Y:%m:%d %H:%M:%S'))
        )
    if game.has_access('needs_registration', team=team) and not game.has_access('is_registered', team=team):
        return HttpResponse(
            status=405,
            content='Чтобы получить доступ к игре, нужно зарегистрировать свою команду на игру <a href="/{}">здесь</a>'.format(
                game.project
            )
        )
    if not game.has_access('play', team=team):
        raise NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    mode = game.get_current_mode(Attempt(time=timezone.now()))

    task_to_attempts_info = get_task_to_attempts_info(game, team, mode)
    
    task_groups = sorted(
        game.task_groups.all() if task_group is None else game.task_groups.filter(number=task_group),
        key=lambda tg: tg.number
    )

    task_group_to_tasks = {}
    for task_group in task_groups:
        task_group_to_tasks[task_group.number] = sorted(
            task_group.tasks.all() if task is None else task_group.tasks.filter(number=task),
            key=lambda t: t.key_sort()
        )

    text_with_forms_to_html = get_all_text_with_forms_to_html(request, game, team, mode)
    return render(request, 'game.html', {
        'team': team,
        'game': game,
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'task_to_attempts_info': task_to_attempts_info,
        'task_text_with_forms_to_html': text_with_forms_to_html["tasks"],
        'task_group_text_with_forms_to_html': text_with_forms_to_html["task_groups"],
        'game_text_with_forms_to_html': text_with_forms_to_html.get("game", None),
        'mode': mode,
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
        'is_one_task': task is not None
    })


def get_tournament_results(request, game_id):
    return results_page(request, game_id, mode='tournament') 