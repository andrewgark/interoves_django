from django.utils import timezone
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from games.exception import DuplicateAttemptException, NotAllRequiredHintsTakenException, NoGameAccessException
from games.models import GameTaskGroup, Hint, HintAttempt, Task, Attempt
from games.views.game_context import game_from_request_for_task
from games.views.render_task import update_task_html
from games.views.track import track_task_change
from games.views.util import effective_play_mode, has_profile, has_team


def _get_play_mode(request, game):
    mode = request.session.get('play_mode_{}'.format(game.project_id or 'main'))
    if mode not in ('team', 'personal'):
        mode = 'personal' if game.project_id == 'sections' else 'team'
    return effective_play_mode(mode, game)


def _hintattempt_filter(team=None, user=None, anon_key=None):
    if team is not None:
        return {'team': team, 'user__isnull': True, 'anon_key__isnull': True}
    if user is not None:
        return {'user': user, 'team__isnull': True, 'anon_key__isnull': True}
    return {'anon_key': anon_key, 'team__isnull': True, 'user__isnull': True}


def create_hint_attempt(hint, team=None, user=None, anon_key=None, game=None):
    task = hint.task
    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        raise Exception('Cannot resolve game for hint (pass game= or single-linked task group)')

    if list(HintAttempt.objects.filter(hint=hint, **_hintattempt_filter(team=team, user=user, anon_key=anon_key))):
        raise DuplicateAttemptException('Вы уже запрашивали эту подсказку')
    
    required_hints = set(hint.required_hints.all())
    if len(HintAttempt.objects.filter(hint__in=required_hints, **_hintattempt_filter(team=team, user=user, anon_key=anon_key))) < len(required_hints):
        raise NotAllRequiredHintsTakenException('Вы не можете пока взять эту подсказку')

    hint_attempt = HintAttempt(team=team, user=user, anon_key=anon_key, hint=hint)
    hint_attempt.save()

    current_mode = game.get_current_mode(hint_attempt)
    attempts_info = Attempt.manager.get_attempts_info(
        team=team, user=user, anon_key=anon_key, task=task, mode=current_mode, game=game,
    )

    hint_attempt.is_real_request = not attempts_info.is_solved()
    hint_attempt.save()
    return hint_attempt, current_mode
   

def process_send_hint_attempt(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    game = game_from_request_for_task(request, task)
    if game is None:
        return {'status': 'ambiguous_game'}
    play_mode = _get_play_mode(request, game)

    team = None
    user = None
    anon_key = None
    if play_mode == 'team':
        if not request.user.is_authenticated or not has_team(request.user):
            return {'status': 'no_team'}
        team = request.user.profile.team_on
        if not game.has_access('send_attempt', team=team):
            return NoGameAccessException('User has no access to game {}'.format(game))
    else:
        if request.user.is_authenticated:
            if not has_profile(request.user):
                return {'status': 'no_profile'}
            user = request.user
        else:
            anon_key = request.POST.get('anon_key') or request.headers.get('X-Interoves-Anon')
            if not anon_key:
                return {'status': 'no_anon'}
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            return NoGameAccessException('User has no access to game {}'.format(game))

    if task.task_type == 'autohint':
        return Exception('Hints in this task can only be taken by answer submit')

    hint_number = int(request.POST['hint_number'])
    hint = get_object_or_404(Hint, task=task, number=hint_number)

    hint_attempt, current_mode = create_hint_attempt(
        hint, team=team, user=user, anon_key=anon_key, game=game,
    )

    result = {
        'status': 'ok',
        'task_id': task.id,
    }
    # Для личного/анонимного режима не обновляем старую HTML-структуру; new UI просто перезагрузит страницу.
    if team is not None:
        update_html = update_task_html(
            request, task, team, current_mode, user=user, anon_key=anon_key, game=game,
        )
        track_task_change(
            task, team, current_mode, update_html=update_html, request=request, game=game,
        )
        result.update(update_html)
    return result


def send_hint_attempt(request, task_id):
    try:
        response = process_send_hint_attempt(request, task_id)
    except DuplicateAttemptException:
        response = {'status': 'duplicate'}
    except NotAllRequiredHintsTakenException:
        response = {'status': 'not_all_required_hints_taken'}
    return JsonResponse(response) 