from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from games.views.game_context import game_from_request_for_task
from games.views.util import get_public_task_or_404, has_profile, has_team


def task_ok_by_team(task, team, mode):
    from games.models import Attempt
    best_attempt = Attempt.manager.get_attempts_info(team=team, task=task, mode=mode).best_attempt
    return best_attempt and best_attempt.status == 'Ok'


def get_answer(request, task_id):
    task = get_public_task_or_404(task_id)
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    game = game_from_request_for_task(request, task)
    if game is None:
        return JsonResponse({'error': 'not found'}, status=404)

    if not game.has_access('play', team=team):
        return JsonResponse({'error': 'no access'}, status=403)

    mode = game.get_current_mode()

    if mode != 'general' and not task_ok_by_team(task, team, mode):
        return JsonResponse({'error': 'answer unavailable'}, status=403)

    return JsonResponse({
        'html': render(request, 'answer.html', {
            'task': task,
        }).content.decode('UTF-8'),
    })
