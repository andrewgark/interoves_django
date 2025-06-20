from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from games.exception import NoGameAccessException, NoAnswerAccessException
from games.models import Task
from games.views.util import has_profile, has_team


def task_ok_by_team(task, team, mode):
    from games.models import Attempt
    best_attempt = Attempt.manager.get_attempts_info(team=team, task=task, mode=mode).best_attempt
    return best_attempt and best_attempt.status == 'Ok'


def get_answer(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    game = task.task_group.game

    if not task.task_group.game.has_access('play', team=team):
        return NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    mode = game.get_current_mode()

    if mode != 'general' and not task_ok_by_team(task, request.user.profile.team_on, mode):
        return NoAnswerAccessException('User {} has no access to answers to task {} right now'.format(request.user.profile, task))

    return JsonResponse({
        'html': render(request, 'answer.html', {
            'task': task,
        }).content.decode('UTF-8'),
    }) 