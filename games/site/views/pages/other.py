from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from games.identity import has_team
from games.models import Like
from games.site.http.request_util import (
    get_public_task_or_404,
    is_browser_form_submission,
    redirect_after_browser_submission,
)
from games.site.views.game.context import game_from_request_for_task


@user_passes_test(has_team)
@require_POST
def like_dislike(request, task_id):
    task = get_public_task_or_404(task_id)

    team = request.user.profile.team_on
    game = game_from_request_for_task(request, task)
    if game is None:
        return JsonResponse({'error': 'not found'}, status=404)

    from games.site.views.game.context import unpublished_scheduled_task_response
    if unpublished_scheduled_task_response(request, game, task) is not None:
        return JsonResponse({'error': 'not found'}, status=404)

    if not game.has_access('send_attempt', team=team):
        return JsonResponse({'error': 'no access'}, status=403)

    from games.site.ui.play.reactions import _posted_reaction
    reaction = _posted_reaction(request)
    if isinstance(reaction, JsonResponse):
        return reaction
    if reaction is not None:
        Like.manager.set_actor_reaction(task, reaction, team=team)

    response = JsonResponse({
        'likes': Like.manager.get_likes(task),
        'dislikes': Like.manager.get_dislikes(task)
    })
    if is_browser_form_submission(request):
        return redirect_after_browser_submission(request)
    return response


# for game 29 :)
def return_intentional_503(request):
    return HttpResponse(status=503)


# for game 54
def easter_egg_2021(request):
    return render(request, 'easter_egg_2021.html')
