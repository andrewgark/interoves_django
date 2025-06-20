from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from games.exception import DuplicateAttemptException, NotAllRequiredHintsTakenException, NoGameAccessException
from games.models import Hint, HintAttempt, Task, Attempt
from games.views.render_task import update_task_html
from games.views.track import track_task_change
from games.views.util import has_team


def create_hint_attempt(hint, team):
    task = hint.task
    game = task.task_group.game

    if list(HintAttempt.objects.filter(team=team, hint=hint)):
        raise DuplicateAttemptException('Вы уже запрашивали эту подсказку')
    
    required_hints = set(hint.required_hints.all())
    if len(HintAttempt.objects.filter(team=team, hint__in=required_hints)) < len(required_hints):
        raise NotAllRequiredHintsTakenException('Вы не можете пока взять эту подсказку')

    hint_attempt = HintAttempt(team=team, hint=hint)
    hint_attempt.save()

    current_mode = game.get_current_mode(hint_attempt)
    attempts_info = Attempt.manager.get_attempts_info(team=team, task=task, mode=current_mode)

    hint_attempt.is_real_request = not attempts_info.is_solved()
    hint_attempt.save()
    return hint_attempt, current_mode
   

@user_passes_test(has_team)
def process_send_hint_attempt(request, task_id):
    task = get_object_or_404(Task, id=task_id)

    team = request.user.profile.team_on
    game = task.task_group.game

    if not task.task_group.game.has_access('send_attempt', team=team):
        return NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    if task.task_type == 'autohint':
        return Exception('Hints in this task can only be taken by answer submit')

    hint_number = int(request.POST['hint_number'])
    hint = get_object_or_404(Hint, task=task, number=hint_number)

    hint_attempt, current_mode = create_hint_attempt(hint, team)

    result = {
        'status': 'ok',
        'task_id': task.id,
    }
    update_html = update_task_html(request, task, team, current_mode)
    track_task_change(task, team, current_mode, update_html=update_html, request=request)
    result.update(update_html)
    return result


@user_passes_test(has_team)
def send_hint_attempt(request, task_id):
    try:
        response = process_send_hint_attempt(request, task_id)
    except DuplicateAttemptException:
        response = {'status': 'duplicate'}
    except NotAllRequiredHintsTakenException:
        response = {'status': 'not_all_required_hints_taken'}
    return JsonResponse(response) 