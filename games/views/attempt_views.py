import json
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from games.analytics import (
    PlayerCompletedGame,
    is_task_completion_state,
    register_completed_game,
    supported_game_kind,
)
from games.check import CheckerFactory
from games.exception import DuplicateAttemptException, TooManyAttemptsException, InvalidFormException, NoGameAccessException
from games.forms import AttemptForm
from games.models import Attempt, ChainTaskState, CheckerType, GameTaskGroup, Task, Team, CHAIN_TASK_TYPES
from games.views.game_context import game_from_request_for_task
from games.views.render_task import update_task_html
from games.views.track import track_task_change
from games.raddle import (
    load_raddle_state,
    parse_raddle_data,
    playable_word_indices,
    raddle_blocks_as_duplicate,
    serialize_raddle_attempt_text,
    word_matches,
)
from games.views.util import effective_play_mode, get_public_task_or_404, has_profile, has_team


def _raddle_chain_state(task, team, user, anon_key, game, current_mode):
    """Актуальный raddle state для актёра (CTS, иначе последняя Attempt.state)."""
    parsed = parse_raddle_data(task)
    if not parsed:
        return None, None
    n = parsed['n_words']
    chain_row = ChainTaskState.objects.filter(
        team=team, user=user, anon_key=anon_key,
        task=task, game=game, game_mode=current_mode,
    ).first()
    if chain_row and chain_row.state:
        return parsed, load_raddle_state(chain_row.state, n)
    attempts = Attempt.manager.get_attempts(
        team, task, mode=current_mode, user=user, anon_key=anon_key, game=game,
    )
    for prev in reversed(attempts):
        if prev.state:
            return parsed, load_raddle_state(prev.state, n)
    return parsed, load_raddle_state(None, n)


def _raddle_stale_submit_response(request, task, team, user, anon_key, game, current_mode, word_index):
    """
    Устаревший UI (bfcache / смена anon↔login): форма на некрайнем или уже
    решённом слове. Не пишем Attempt — только синхронизируем HTML.
    """
    parsed, state = _raddle_chain_state(task, team, user, anon_key, game, current_mode)
    if not parsed or state is None:
        return None
    solved = set(state.get('solved_indices') or [])
    playable = playable_word_indices(state, parsed['n_words'])
    # Уже решённые оставляем существующим путям (duplicate_solved / needs_sync).
    if word_index not in playable and word_index not in solved:
        result = {
            'status': 'ok',
            'task_id': task.id,
            'raddle_correct': False,
            'raddle_needs_sync': True,
            'raddle_stale_ui': True,
            'raddle_word_index': word_index,
        }
        update_html = update_task_html(
            request, task, team, current_mode, user=user, anon_key=anon_key, game=game,
        )
        track_task_change(
            task, team, current_mode, update_html=update_html, request=request, game=game,
        )
        result.update(update_html)
        return result
    return None


def check_attempt(attempt, *, persist_wrong=True):
    task = attempt.task
    team = attempt.team
    user = getattr(attempt, 'user', None)
    anon_key = getattr(attempt, 'anon_key', None)
    game = attempt.game or GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        raise Exception('Cannot resolve game for attempt (set Attempt.game or use a single-linked task group)')

    current_mode = game.get_current_mode(attempt)
    if attempt._state.adding and attempt.task_revision is None:
        attempt.task_revision = task.attempt_revision
    attempt_revision = attempt.task_revision or task.attempt_revision
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
            revision_attempts = [
                previous for previous in attempts
                if previous.task_revision == attempt_revision
            ]

            # Пустой ChainTaskState после anon-migrate: подтянуть state из попыток
            # текущего режима (не general→tournament — иначе ломается изоляция).
            if (
                is_chain_task
                and last_attempt_state is None
                and mode == current_mode
                and attempts
            ):
                for prev in reversed(attempts):
                    if prev.state:
                        last_attempt_state = prev.state
                        break
            elif not is_chain_task and mode == 'general' and attempts:
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
                    for a in revision_attempts:
                        try:
                            p = json.loads(a.text)
                            if int(p.get('line_index', -1)) == current_line:
                                n_attempts_this_line += 1
                        except (ValueError, TypeError):
                            pass
                    max_attempts = task.get_max_attempts()
                    if n_attempts_this_line >= max_attempts:
                        raise TooManyAttemptsException('Team {} exceeds attempts limit ({}) in task {} for line {}'.format(team, max_attempts, task, current_line + 1))
                elif task.task_type == 'raddle':
                    try:
                        current_payload = json.loads(attempt.text)
                        current_word = int(current_payload.get('word_index', -1))
                    except (ValueError, TypeError):
                        current_word = -1
                    n_attempts_this_word = 0
                    for a in revision_attempts:
                        try:
                            p = json.loads(a.text)
                            if int(p.get('word_index', -1)) == current_word:
                                n_attempts_this_word += 1
                        except (ValueError, TypeError):
                            pass
                    max_attempts = task.get_max_attempts()
                    if n_attempts_this_word >= max_attempts:
                        raise TooManyAttemptsException('Team {} exceeds attempts limit ({}) in task {} for word {}'.format(team, max_attempts, task, current_word + 1))
                else:
                    n_attempts = len(revision_attempts)
                    max_attempts = task.get_max_attempts()
                    if n_attempts >= max_attempts:
                        raise TooManyAttemptsException('Team {} exceeds attempts limit ({}) in task {}'.format(team, max_attempts, task))

            for other_attempt in revision_attempts:
                if task.task_type == 'raddle':
                    if raddle_blocks_as_duplicate(
                        attempt.text, other_attempt.text,
                        task=task, state_raw=last_attempt_state,
                        other_attempt=other_attempt,
                    ):
                        raise DuplicateAttemptException(
                            'Attempt duplicates one of the previous attempts by this team'
                        )
                elif attempt.text == other_attempt.text:
                    raise DuplicateAttemptException(
                        'Attempt duplicates one of the previous attempts by this team'
                    )

        checker_type = task.get_checker()
        if task.task_type == 'replacements_lines':
            checker_type = CheckerType.objects.get(id='replacements_lines')
        if task.task_type == 'raddle':
            checker_type = CheckerType.objects.get(id='raddle')
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
        from decimal import Decimal
        # Word Salad has an intrinsic scale: +1 per word and -0.5 per hint.
        if task.task_type == 'word_salad':
            attempt.points = Decimal(str(attempt.points or 0))
        else:
            attempt.points = Decimal(str(attempt.points or 0)) * task.get_points()

        # Auto-checking controls may probe a candidate without turning every typo
        # into an Attempt. The checker still runs under the chain-state lock, but
        # only a result that advances the task is committed.
        if not persist_wrong and check_result.status in ('Wrong', 'Pending'):
            return False

        attempt.save()

        if chain_state_row is not None:
            chain_state_row.state = attempt.state
            chain_state_row.last_attempt = attempt
            chain_state_row.save(update_fields=['state', 'last_attempt', 'updated_at'])
        return True

    if is_chain_task:
        with transaction.atomic():
            persisted = _run()
    else:
        persisted = _run()

    if not persisted:
        return False

    # if some task had tag on this task, recheck it too
    if task.task_type == 'with_tag':
        tag_task_number = task.tags.get('task')
        tag_team_name = task.tags.get('team')
        if tag_task_number is None or tag_team_name is None:
            return True
        try:
            tag_task = Task.objects.visible().get(
                task_group=task.task_group, checker_data__contains=tag_task_number,
            )
            tag_team = Team.objects.get(name=tag_team_name)
            assert tag_task.task_type != 'with_tag'
        except Exception:
            return True

        for tag_attempt in Attempt.manager.filter(task=tag_task, team=tag_team, game=game):
            check_attempt(tag_attempt)
    return True


def get_first_new_hint(task, team):
    from games.models import Hint, HintAttempt
    hints = Hint.objects.filter(task=task)
    hints = sorted(hints, key=lambda h: h.key_sort())
    for hint in hints:
        if len(HintAttempt.objects.filter(team=team, hint=hint)) == 0:
            return hint
    return None


def get_first_new_hint_actor(task, team=None, user=None, anon_key=None):
    from games.models import Hint, HintAttempt
    hints = Hint.objects.filter(task=task)
    hints = sorted(hints, key=lambda h: h.key_sort())
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
    elif task.task_type == 'raddle':
        word_index = int(request.POST.get('word_index', 0))
        word = (request.POST.get('word') or '').strip()
        if not word:
            return {'status': 'empty'}
        attempt = Attempt(text=serialize_raddle_attempt_text(word_index, word))
    elif task.task_type == 'alphabetty':
        word = (request.POST.get('word') or request.POST.get('text') or '').strip()
        if not word:
            return {'status': 'empty'}
        attempt = Attempt(text=word)
    elif task.task_type == 'word_salad':
        action = (request.POST.get('action') or 'solve').strip().lower()
        if action == 'hint':
            try:
                word_index = int(request.POST.get('word_index', -1))
            except (TypeError, ValueError):
                return {'status': 'empty'}
            if word_index < 0:
                return {'status': 'empty'}
            hint_payload = {'action': 'hint', 'word_index': word_index}
            hint_number = request.POST.get('hint_number')
            if hint_number not in (None, ''):
                try:
                    hint_payload['hint_number'] = int(hint_number)
                except (TypeError, ValueError):
                    return {'status': 'empty'}
            attempt = Attempt(text=json.dumps(hint_payload))
        else:
            path_raw = request.POST.get('path') or request.POST.get('path_json') or '[]'
            if isinstance(path_raw, str):
                try:
                    path = json.loads(path_raw)
                except (TypeError, ValueError):
                    path = []
            else:
                path = list(path_raw)
            try:
                path = [int(value) for value in path]
            except (TypeError, ValueError):
                return {'status': 'empty'}
            if not path:
                return {'status': 'empty'}
            attempt = Attempt(text=json.dumps({'action': 'solve', 'path': path}))
    else:
        raise Exception('Unknown task_type: {}'.format(task.task_type))
    attempt.team = team
    attempt.user = user
    attempt.anon_key = anon_key
    attempt.task = task
    attempt.time = timezone.now()
    attempt.game = game

    current_mode = game.get_current_mode(attempt)

    if task.task_type == 'raddle':
        stale = _raddle_stale_submit_response(
            request, task, team, user, anon_key, game, current_mode, word_index,
        )
        if stale is not None:
            return stale

    correct_only = (
        task.task_type == 'word_salad'
        and action == 'solve'
        and request.POST.get('correct_only') == '1'
    )
    attempt_persisted = check_attempt(attempt, persist_wrong=not correct_only)

    if attempt_persisted and task.task_type == 'autohint' and attempt.status in ('Pending', 'Wrong'):
        hint = get_first_new_hint_actor(task, team=team, user=user, anon_key=anon_key)
        if hint is not None:
            from games.views.hint_views import create_hint_attempt
            create_hint_attempt(hint, team=team, user=user, anon_key=anon_key, game=game)

    analytics_events = []
    if attempt_persisted and supported_game_kind(game) and is_task_completion_state(task, attempt.state):
        analytics_events = register_completed_game(
            team=team,
            user=user,
            anon_key=anon_key,
            analytics_user=request.user if request.user.is_authenticated else None,
            task=task,
            game=game,
            result=PlayerCompletedGame.RESULT_SOLVED,
        )

    result = {
        'status': 'ok',
        'task_id': task.id,
    }
    if analytics_events:
        result['analytics_events'] = analytics_events
    if task.task_type == 'raddle':
        # Partial = «есть прогресс по заданию», не «эта попытка верна».
        raddle_correct = False
        try:
            st = json.loads(attempt.state or '{}')
            solved = set(st.get('solved_indices') or [])
            parsed = parse_raddle_data(task)
            if word_index in solved and parsed:
                raddle_correct = word_matches(word, parsed['word_accept'][word_index])
        except (ValueError, TypeError, IndexError, KeyError):
            pass
        result['raddle_correct'] = raddle_correct
        result['raddle_word_index'] = word_index
        if not result['raddle_correct']:
            try:
                st = json.loads(attempt.state or '{}')
                if word_index in set(st.get('solved_indices') or []):
                    result['raddle_needs_sync'] = True
            except (ValueError, TypeError):
                pass
    if task.task_type == 'word_salad':
        result['word_salad_correct'] = bool(attempt_persisted)
        if not attempt_persisted and attempt.comment:
            result['word_salad_comment'] = attempt.comment

    # Raddle wrong answers: client updates locally (showRaddleWrongFeedback); skip ~40KB HTML.
    need_task_html = (
        task.task_type != 'raddle' or result.get('raddle_correct') or result.get('raddle_needs_sync')
    ) and (
        task.task_type != 'word_salad' or result.get('word_salad_correct')
    )
    if need_task_html:
        update_html = update_task_html(
            request, task, team, current_mode, user=user, anon_key=anon_key, game=game,
        )
        track_task_change(
            task, team, current_mode, update_html=update_html, request=request, game=game,
        )
        result.update(update_html)
    return result


def _raddle_duplicate_response(request, task_id):
    """Duplicate для raddle: синхронизируем UI только если слово уже решено (лаг сети)."""
    task = get_public_task_or_404(task_id)
    if task.task_type != 'raddle':
        return {}
    try:
        word_index = int(request.POST.get('word_index', -1))
    except (TypeError, ValueError):
        word_index = -1

    game = game_from_request_for_task(request, task)
    if game is None:
        return {'raddle_word_index': word_index}
    play_mode = _get_play_mode(request, game)
    team = user = anon_key = None
    if play_mode == 'team':
        if not request.user.is_authenticated or not has_team(request.user):
            return {'raddle_word_index': word_index}
        team = request.user.profile.team_on
    else:
        if request.user.is_authenticated:
            if not has_profile(request.user):
                return {'raddle_word_index': word_index}
            user = request.user
        else:
            anon_key = request.POST.get('anon_key') or request.headers.get('X-Interoves-Anon')
            if not anon_key:
                return {'raddle_word_index': word_index}
    current_mode = game.get_current_mode(Attempt(time=timezone.now()))

    parsed = parse_raddle_data(task)
    solved = False
    if parsed and word_index >= 0:
        chain_row = ChainTaskState.objects.filter(
            team=team, user=user, anon_key=anon_key,
            task=task, game=game, game_mode=current_mode,
        ).first()
        state = load_raddle_state(
            chain_row.state if chain_row else None,
            parsed['n_words'],
        )
        solved = word_index in set(state.get('solved_indices') or [])

    result = {
        'raddle_word_index': word_index,
        'raddle_duplicate_solved': solved,
    }
    if solved:
        result.update(update_task_html(
            request, task, team, current_mode, user=user, anon_key=anon_key, game=game,
        ))
    return result


def send_attempt(request, task_id):
    try:
        response = process_send_attempt(request, task_id)
    except DuplicateAttemptException:
        response = {'status': 'duplicate'}
        response.update(_raddle_duplicate_response(request, task_id))
    except TooManyAttemptsException:
        response = {'status': 'attempt_limit_exceeded'}
    except InvalidFormException:
        response = {'status': 'invalid_form'}
    except NoGameAccessException:
        response = {'status': 'no_access'}
    return JsonResponse(response) 
