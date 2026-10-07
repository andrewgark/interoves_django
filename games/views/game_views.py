import datetime
import json
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.views import defaults
from django.utils import timezone
from games.access import game_has_started
from games.exception import NoGameAccessException
from games.models import Game, Team, Attempt, ImageManager, AudioManager
from games.project_navigation import non_root_project_game_path
from games.views.render_task import get_task_to_attempts_info, get_all_text_with_forms_to_html
from games.gameplay_context import issue_gameplay_context
from games.html_forms import render_html_forms_task
from games.views.team_views import get_team_to_play_page
from games.views.util import has_profile, has_team
from games.views.results_views import results_page


def game_page(request, game_id, task_group=None, task=None):
    game = get_object_or_404(Game, id=game_id)
    suffix = ''
    if task_group is not None:
        suffix = '{}/'.format(task_group)
    redirect_path = non_root_project_game_path(game, suffix)
    if redirect_path:
        query = request.META.get('QUERY_STRING')
        if query:
            redirect_path = '{}?{}'.format(redirect_path, query)
        return redirect(redirect_path, permanent=True)

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
    
    links_qs = game.task_group_links.select_related('task_group')
    if task_group is not None:
        links_qs = links_qs.filter(number=task_group)
    task_group_placements = sorted(links_qs, key=lambda p: p.key_sort())

    task_group_to_tasks = {}
    for placement in task_group_placements:
        tg = placement.task_group
        task_group_to_tasks[placement.number] = sorted(
            tg.tasks.visible() if task is None else tg.tasks.visible().filter(number=task),
            key=lambda t: t.key_sort()
        )

    text_with_forms_to_html = get_all_text_with_forms_to_html(request, game, team, mode)
    gameplay_context_tokens = {}
    task_html_forms_to_html = {}
    for tasks in task_group_to_tasks.values():
        for item in tasks:
            gameplay_context_tokens[item.id] = issue_gameplay_context(
                task=item,
                game=game,
                team=team,
                user=request.user if request.user.is_authenticated else None,
                anon_key=None,
            )
            if item.task_type == 'html_forms':
                task_html_forms_to_html[item.id] = render_html_forms_task(
                    request,
                    item,
                    task_to_attempts_info.get(item.id),
                    gameplay_context_tokens[item.id],
                    game=game,
                )
    return render(request, 'game.html', {
        'team': team,
        'game': game,
        'task_group_placements': task_group_placements,
        'task_group_to_tasks': task_group_to_tasks,
        'task_to_attempts_info': task_to_attempts_info,
        'task_text_with_forms_to_html': text_with_forms_to_html["tasks"],
        'task_html_forms_to_html': task_html_forms_to_html,
        'task_group_text_with_forms_to_html': text_with_forms_to_html["task_groups"],
        'game_text_with_forms_to_html': text_with_forms_to_html.get("game", None),
        'mode': mode,
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
        'is_one_task': task is not None,
        'gameplay_context_tokens': gameplay_context_tokens,
    })


def get_tournament_results(request, game_id):
    return results_page(request, game_id, mode='tournament')
