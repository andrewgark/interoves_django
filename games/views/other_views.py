from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from games.exception import NoGameAccessException
from games.models import Like
from games.views.game_context import game_from_request_for_task
from games.views.util import get_public_task_or_404, has_team


@user_passes_test(has_team)
def like_dislike(request, task_id):
    task = get_public_task_or_404(task_id)

    team = request.user.profile.team_on
    game = game_from_request_for_task(request, task)
    if game is None:
        return NoGameAccessException('Cannot resolve game for task {}'.format(task.id))

    if not game.has_access('send_attempt', team=team):
        return NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    likes = int(request.POST.get('likes', 0))
    dislikes = int(request.POST.get('dislikes', 0))
    if likes == 1:
        reaction = 1
    elif dislikes == 1:
        reaction = -1
    elif likes == -1 or dislikes == -1:
        reaction = 0
    else:
        reaction = None
    if reaction is not None:
        Like.manager.set_actor_reaction(task, reaction, team=team)

    return JsonResponse({
        'likes': Like.manager.get_likes(task),
        'dislikes': Like.manager.get_dislikes(task)
    })


# for game 29 :)
def return_intentional_503(request):
    return HttpResponse(status=503)


# for game 54
def easter_egg_2021(request):
    return render(request, 'easter_egg_2021.html')
