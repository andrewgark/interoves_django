import json
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from games.check import CheckerFactory
from games.exception import DuplicateAttemptException, TooManyAttemptsException, InvalidFormException, NoGameAccessException
from games.forms import AttemptForm
from games.models import Attempt, ChainTaskState, CheckerType, GameTaskGroup, Task, Team, CHAIN_TASK_TYPES
from games.views.game_context import game_from_request_for_task
from games.views.render_task import update_task_html
from games.views.track import track_task_change
from games.views.util import effective_play_mode, get_public_task_or_404, has_profile, has_team


def check_attempt(attempt):
    task = attempt.task
    team = attempt.team
    user = getattr(attempt, 'user', None)
    anon_key = getattr(attempt, 'anon_key', None)
    game = attempt.game or GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        raise Exception('Cannot resolve game for attempt (set Attempt.game or use a single-linked task group)')

    current_mode = game.get_current_mode(attempt)
    modes = ['general']
    if current_mode == 'tournament':
        modes.append('tournament')

    is_chain_task = task.task_type in CHAIN_TASK_TYPES

    def _run():
        last_attempt_state = None
        chain_state_row = None

        if is_chain_task:
            # Ensure the state row exists, then acquire an exclusive row lock.
            # Two-step pattern avoids needing a single unique_together over nullable fields.
            ChainTaskState.objects.get_or_create(
                team=team, user=user, anon_key=anon_key,
                task=task, game=game, game_mode=current_mode,
                defaults={'state': None},
            )
            chain_state_row = ChainTaskState.objects.select_for_update().get(
                team=team, user=user, anon_key=anon_key,
                task=task, game=game, game_mode=current_mode,
            )
            last_attempt_state = chain_state_row.state

        for mode in modes:
            attempts = Attempt.manager.get_attempts_before(
                team, task, attempt.time, mode, user=user, anon_key=anon_key, game=game,
            )

            if not is_chain_task and mode == 'general' and attempts:
                if last_attempt_state is None:
                    last_attempt_state = attempts[-1].state

            if mode == 'tournament':
                if task.task_type == 'wall':
                    current_state = chain_state_row.state if chain_state_row else None
                    validation_data = task.get_wall().validate_max_attempts(
                        attempts, attempt, current_state=current_state,
                    )
                    if validation_data is not None:
                        stage, n_attempts, max_attempts = validation_data
                        raise TooManyAttemptsException('Team {} exceeds attempts limit ({}) in wall task {} on stage {}'.format(team, max_attempts, task, stage))
                elif task.task_type == 'replacements_lines':
                    try:
                        current_payload = json.loads(attempt.text)
                        current_line = int(current_payload.get('line_index', -1))
                    except (ValueError, TypeError):
                        current_line = -1
                    n_attempts_this_line = 0
                    for a in attempts:
                        try:
                            p = json.loads(a.text)
                            if int(p.get('line_index', -1)) == current_line:
                                n_attempts_this_line += 1
                        except (ValueError, TypeError):
                            pass
                    max_attempts = task.get_max_attempts()
                    if n_attempts_this_line >= max_attempts:
                        raise TooManyAttemptsException('Team {} exceeds attempts limit ({}) in task {} for line {}'.format(team, max_attempts, task, current_line + 1))
                else:
                    n_attempts = len(attempts)
                    max_attempts = task.get_max_attempts()
                    if n_attempts >= max_attempts:
                        raise TooManyAttemptsException('Team {} exceeds attempts limit ({}) in task {}'.format(team, max_attempts, task))

            for other_attempt in attempts:
                if attempt.text == other_attempt.text:
                    raise DuplicateAttemptException('Attempt duplicates one of the previous attempts by this team')

        checker_type = task.get_checker()
        if task.task_type == 'replacements_lines':
            checker_type = CheckerType.objects.get(id='replacements_lines')
        checker_data = task.checker_data or ''
        # equals / equals_with_possible_spaces читают эталон из checker_data; для «Пропорций»
        # и обычных заданий ответ часто задают только в answer — тогда дублируем его сюда.
        if checker_type.id in ('equals', 'equals_with_possible_spaces'):
            if not checker_data.strip() and (task.answer or '').strip():
                checker_data = task.answer
        checker = CheckerFactory().create_checker(checker_type, checker_data, last_attempt_state)
        check_result = checker.check(attempt.text, attempt)
        attempt.status, attempt.points, attempt.state, attempt.comment = check_result.status, check_result.points, check_result.state, check_result.comment
        if 'tournament' in modes and attempt.status != 'Ok':
            attempt.possible_status = attempt.status
            attempt.status = check_result.tournament_status
        attempt.points *= task.get_points()

        attempt.save()

        if chain_state_row is not None:
            chain_state_row.state = attempt.state
            chain_state_row.last_attempt = attempt
            chain_state_row.save(update_fields=['state', 'last_attempt', 'updated_at'])

    if is_chain_task:
        with transaction.atomic():
            _run()
    else:
        _run()

    # if some task had tag on this task, recheck it too
    if task.task_type == 'with_tag':
        tag_task_number = task.tags.get('task')
        tag_team_name = task.tags.get('team')
        if tag_task_number is None or tag_team_name is None:
            return
        try:
            tag_task = Task.objects.visible().get(
                task_group=task.task_group, checker_data__contains=tag_task_number,
            )
            tag_team = Team.objects.get(name=tag_team_name)
            assert tag_task.task_type != 'with_tag'
        except:
            return

        for tag_attempt in Attempt.manager.filter(task=tag_task, team=tag_team, game=game):
            check_attempt(tag_attempt)


def get_first_new_hint(task, team):
    from games.models import Hint, HintAttempt
    hints = Hint.objects.filter(task=task)
    hints = sorted(hints, key=lambda h: h.number)
    for hint in hints:
        if len(HintAttempt.objects.filter(team=team, hint=hint)) == 0:
            return hint
    return None


def get_first_new_hint_actor(task, team=None, user=None, anon_key=None):
    from games.models import Hint, HintAttempt
    hints = Hint.objects.filter(task=task)
    hints = sorted(hints, key=lambda h: h.number)
    for hint in hints:
        if team is not None:
            exists = HintAttempt.objects.filter(team=team, user__isnull=True, anon_key__isnull=True, hint=hint).exists()
        elif user is not None:
            exists = HintAttempt.objects.filter(user=user, team__isnull=True, anon_key__isnull=True, hint=hint).exists()
        else:
            exists = HintAttempt.objects.filter(anon_key=anon_key, team__isnull=True, user__isnull=True, hint=hint).exists()
        if not exists:
            return hint
    return None


def _get_play_mode(request, game):
    mode = request.session.get('play_mode_{}'.format(game.project_id or 'main'))
    if mode not in ('team', 'personal'):
        mode = 'personal' if game.project_id == 'sections' else 'team'
    return effective_play_mode(mode, game)


def process_send_attempt(request, task_id):
    task = get_public_task_or_404(task_id)

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
    else:
        if request.user.is_authenticated:
            if not has_profile(request.user):
                return {'status': 'no_profile'}
            user = request.user
        else:
            anon_key = request.POST.get('anon_key') or request.headers.get('X-Interoves-Anon')
            if not anon_key:
                return {'status': 'no_anon'}

    if play_mode == 'team':
        if not game.has_access('send_attempt', team=team):
            raise NoGameAccessException('User has no access to game {}'.format(game))
    else:
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise NoGameAccessException('User has no access to game {}'.format(game))

    if task.task_type in ('default', 'with_tag', 'distribute_to_teams', 'autohint', 'proportions'):
        form = AttemptForm(request.POST)
        if not form.is_valid():
            raise InvalidFormException('attempt form {} is not valid'.format(form))

        attempt = form.save(commit=False)
    elif task.task_type == 'wall':
        if 'text' not in request.POST:            
            request_data = {
                'words': sorted(request.POST.getlist('words[]')),
                'stage': request.POST['stage'],
            }
        else:
            request_data = {
                'explanation': request.POST['text'],
                'words': json.loads(request.POST['words']),
                'stage': request.POST['stage'],
            }
        attempt = Attempt(text=json.dumps(request_data))
    elif task.task_type == 'replacements_lines':
        line_index = int(request.POST.get('line_index', 0))
        answers_raw = request.POST.get('answers')
        if answers_raw is not None:
            try:
                answers = json.loads(answers_raw)
            except (ValueError, TypeError):
                answers = request.POST.getlist('answers[]')
        else:
            answers = request.POST.getlist('answers[]')
        answers = list(answers)
        if not answers or all(str(a).strip() == '' for a in answers):
            return {'status': 'empty'}
        attempt = Attempt(text=json.dumps({'line_index': line_index, 'answers': answers}))
    else:
        raise Exception('Unknown task_type: {}'.format(task.task_type))
    attempt.team = team
    attempt.user = user
    attempt.anon_key = anon_key
    attempt.task = task
    attempt.time = timezone.now()
    attempt.game = game

    current_mode = game.get_current_mode(attempt)

    check_attempt(attempt)

    if task.task_type == 'autohint' and attempt.status in ('Pending', 'Wrong'):
        hint = get_first_new_hint_actor(task, team=team, user=user, anon_key=anon_key)
        if hint is not None:
            from games.views.hint_views import create_hint_attempt
            create_hint_attempt(hint, team=team, user=user, anon_key=anon_key, game=game)

    result = {
        'status': 'ok',
        'task_id': task.id,
    }
    update_html = update_task_html(
        request, task, team, current_mode, user=user, anon_key=anon_key, game=game,
    )
    track_task_change(
        task, team, current_mode, update_html=update_html, request=request, game=game,
    )
    result.update(update_html)
    return result


def send_attempt(request, task_id):
    try:
        response = process_send_attempt(request, task_id)
    except DuplicateAttemptException:
        response = {'status': 'duplicate'}
    except TooManyAttemptsException:
        response = {'status': 'attempt_limit_exceeded'}
    except InvalidFormException:
        response = {'status': 'invalid_form'}
    except NoGameAccessException:
        response = {'status': 'no_access'}
    return JsonResponse(response) 