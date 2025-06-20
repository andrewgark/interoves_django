from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from games.exception import NoGameAccessException
from games.models import Like, Task
from games.views.util import has_team


@user_passes_test(has_team)
def like_dislike(request, task_id):
    task = get_object_or_404(Task, id=task_id)

    team = request.user.profile.team_on
    game = task.task_group.game

    if not task.task_group.game.has_access('send_attempt', team=team):
        return NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    likes = int(request.POST.get('likes', 0))
    dislikes = int(request.POST.get('dislikes', 0))
    if likes == 1:
        Like.manager.add_like(task, team)
    elif likes == -1:
        Like.manager.delete_like(task, team)
    if dislikes == 1:
        Like.manager.add_dislike(task, team)
    elif dislikes == -1:
        Like.manager.delete_dislike(task, team)

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