import json
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.views import defaults, View
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django_telegram_login.widgets.constants import SMALL
from django_telegram_login.widgets.generator import create_callback_login_widget, create_redirect_login_widget
from games.check import CheckerFactory
from games.exception import *
from games.forms import CreateTeamForm, JoinTeamForm, AttemptForm, TicketRequestForm
from games.models import Team, Game, Attempt, AttemptsInfo, Task, \
    Like, Hint, HintAttempt, ImageManager, AudioManager, Project
from games.views.util import redirect_to_referer, has_profile, has_team
from interoves_django.settings import TELEGRAM_BOT_NAME


class MainPageView(View):
    project_name = 'main'

    def get_games_list(self, request):
        team = None
        if has_profile(request.user):
            team = request.user.profile.team_on

        games_list = []

        project = get_object_or_404(Project, id=self.project_name)
        for game in Game.objects.filter(project=project):
            if game.has_access('see_game_preview', team=team):
                games_list.append(game)
        return sorted(games_list, key=lambda game: (game.start_time, game.name), reverse=True)

    def get(self, request, *args, **kwargs):
        project = get_object_or_404(Project, id=self.project_name)
        return render(request, 'index.html', {
            'create_team_form': CreateTeamForm(project),
            'join_team_form': JoinTeamForm(project),
            'ticket_request_form': TicketRequestForm(),
            'games': self.get_games_list(request),
            'today': timezone.now(),
            'project': project,
            'telegram_login_widget': create_redirect_login_widget(
                project.get_url(), TELEGRAM_BOT_NAME, size=SMALL, user_photo=True
            )
        })


@user_passes_test(has_profile)
def create_team(request):
    user = request.user
    form = CreateTeamForm(project=request.POST.get('project'), data=request.POST)
    if form.is_valid() and not user.profile.team_on:
        team = form.save()
        user.profile.team_on = team
        user.profile.team_requested = None
        user.profile.save()
    return redirect_to_referer(request)


@user_passes_test(has_profile)
def join_team(request):
    user = request.user
    form = JoinTeamForm(project=request.POST.get('project'), data=request.POST)
    if form.is_valid() and form.cleaned_data['name'] and \
       not user.profile.team_on and not user.profile.team_requested:
        team = get_object_or_404(Team, name=form.cleaned_data['name'])
        user.profile.team_requested = team
        user.profile.save()
    return redirect_to_referer(request)


@user_passes_test(has_profile)
def quit_from_team(request):
    user = request.user
    user.profile.team_on = None
    user.profile.team_requested = None
    user.profile.save()
    return redirect_to_referer(request)


@user_passes_test(has_team)
def process_user_request(request, user_id, action):
    active_user = request.user
    passive_user = get_object_or_404(get_user_model(), id=int(user_id))
    if has_profile(passive_user):
        if active_user != passive_user and \
           active_user.profile.team_on == passive_user.profile.team_requested:
            passive_user.profile.team_requested = None
            if action == 'confirm':
                passive_user.profile.team_on = active_user.profile.team_on
            else:
                passive_user.profile.team_on = None
            passive_user.profile.save()
    return redirect_to_referer(request)


def confirm_user_joining_team(request, user_id):
    return process_user_request(request, user_id, 'confirm')


def reject_user_joining_team(request, user_id):
    return process_user_request(request, user_id, 'reject')


def get_task_to_attempts_info(game, team, mode='general'):
    task_to_attempts_info = {}
    for task_group in game.task_groups.all():
        for task in task_group.tasks.all():
            task_to_attempts_info[task.id] = Attempt.manager.get_attempts_info(team=team, task=task, mode=mode)
    return task_to_attempts_info


def get_team_to_play_page(request, game):
    return render(request, 'get_team_to_play.html', {
        'game': game,
        'create_team_form': CreateTeamForm(game.project),
        'join_team_form': JoinTeamForm(game.project),
        'project': game.project
    })


def get_text_with_forms_to_html(request, task_text_with_forms, team, mode):
    htmls = []
    normal_tasks = {int(task.number): task for task in task_text_with_forms.task_group.tasks.all()}
    for i, text_part in enumerate(task_text_with_forms.text.split('______')):
        htmls.append(text_part)
        if (i + 1) not in normal_tasks:
            continue
        task = normal_tasks[i + 1]
        htmls.append(
            render(
                request, 
                'task-content/attempt-simple-form.html',
                {
                    'task': task,
                    'attempts_info': Attempt.manager.get_attempts_info(team=team, task=task, mode=mode)
                }
            ).content.decode('UTF-8')
        )
    return ''.join(htmls)


def get_all_text_with_forms_to_html(request, game, team, mode):
    tasks = []
    for task_group in game.task_groups.all():
        tasks.extend(list(task_group.tasks.filter(task_type='text_with_forms')))
    if not tasks:
        return {}
    result = {}
    for task_text_with_forms in tasks:
        result[task_text_with_forms.id] = get_text_with_forms_to_html(request, task_text_with_forms, team, mode)
    return result


def game_page(request, game_id, task_group=None, task=None):
    game = get_object_or_404(Game, id=game_id)
    # if not has_profile(request.user) or not request.user.profile.team_on:
        # return get_team_to_play_page(request, game)
    team = None
    if has_team(request.user):
        team = request.user.profile.team_on
    if not game.has_access('play', team=team):
        raise NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    mode = game.get_current_mode(Attempt(time=timezone.now()))

    task_to_attempts_info = get_task_to_attempts_info(game, team, mode)
    
    task_groups = sorted(
        game.task_groups.all() if task_group is None else game.task_groups.filter(number=task_group),
        key=lambda tg: tg.number
    )

    task_group_to_tasks = {}
    for task_group in task_groups:
        task_group_to_tasks[task_group.number] = sorted(
            task_group.tasks.all() if task is None else task_group.tasks.filter(number=task),
            key=lambda t: t.key_sort()
        )
    
    return render(request, 'game.html', {
        'team': team,
        'game': game,
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'task_to_attempts_info': task_to_attempts_info,
        'task_text_with_forms_to_html': get_all_text_with_forms_to_html(request, game, team, mode),
        'mode': mode,
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
    })


def check_attempt(attempt):
    task = attempt.task
    team = attempt.team
    game = task.task_group.game

    current_mode = game.get_current_mode(attempt)
    modes = ['general']
    if current_mode == 'tournament':
        modes.append('tournament')

    last_attempt_state = None
    for mode in modes:
        attempts = Attempt.manager.get_attempts_before(team, task, attempt.time, mode)
        if mode == 'general' and attempts:
            last_attempt_state = attempts[-1].state

        if mode == 'tournament':
            if task.task_type == 'wall':
                validation_data = task.get_wall().validate_max_attempts(attempts, attempt)
                if validation_data is not None:
                    stage, n_attempts, max_attempts = validation_data
                    raise TooManyAttemptsException('Team {} exceeds attempts limit ({}) in wall task {} on stage {}'.format(team, max_attempts, task, stage))
            else:
                n_attempts = len(attempts)
                max_attempts = task.get_max_attempts()
                if n_attempts >= max_attempts:
                    raise TooManyAttemptsException('Team {} exceeds attempts limit ({}) in task {}'.format(team, max_attempts, task))

        for other_attempt in attempts:
            if attempt.text == other_attempt.text:
                raise DuplicateAttemptException('Attempt duplicates one of the previous attempts by this team')

    checker = CheckerFactory().create_checker(task.get_checker(), task.checker_data, last_attempt_state)
    check_result = checker.check(attempt.text)
    attempt.status, attempt.points, attempt.state, attempt.comment = check_result.status, check_result.points, check_result.state, check_result.comment
    if 'tournament' in modes and attempt.status != 'Ok':
        attempt.possible_status = attempt.status
        attempt.status = check_result.tournament_status
    attempt.points *= task.get_points()

    attempt.save()


def render_task(task, request, team, current_mode):
    task_text_with_forms_to_html = {}
    if task.task_type == 'text_with_forms':
        task_text_with_forms_to_html = {task.id: get_text_with_forms_to_html(request, task, team, current_mode)}
    return render(request, 'task.html', {
        'task': task,
        'task_group': task.task_group,
        'attempts_info': Attempt.manager.get_attempts_info(team=team, task=task, mode=current_mode),
        'mode': current_mode,
        'team': team,
        'task_text_with_forms_to_html': task_text_with_forms_to_html,
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
    }).content.decode('UTF-8')


@user_passes_test(has_team)
def process_send_attempt(request, task_id):
    task = get_object_or_404(Task, id=task_id)

    team = request.user.profile.team_on
    game = task.task_group.game

    if not task.task_group.game.has_access('send_attempt', team=team):
        return NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    if task.task_type == 'default':
        form = AttemptForm(request.POST)
        if not form.is_valid():
            return InvalidFormException('attempt form {} is not valid'.format(form))

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
    else:
        raise Exception('Unknown task_type: {}'.format(task.task_type))
    attempt.team = team
    attempt.task = task
    attempt.time = timezone.now()

    current_mode = game.get_current_mode(attempt)

    check_attempt(attempt)

    update_extra_tasks = list(task.task_group.tasks.filter(task_type='text_with_forms'))

    return {
        'status': 'ok',
        'task_id': task.id,
        'update_task_html': {
            task.id: render_task(task, request, team, current_mode)
            for task in [task] + update_extra_tasks
        },
    }


@user_passes_test(has_team)
def send_attempt(request, task_id):
    try:
        response = process_send_attempt(request, task_id)
    except DuplicateAttemptException:
        response = {'status': 'duplicate'}
    except TooManyAttemptsException:
        response = {'status': 'attempt_limit_exceeded'}
    return JsonResponse(response)


@user_passes_test(has_team)
def process_send_hint_attempt(request, task_id):
    task = get_object_or_404(Task, id=task_id)

    team = request.user.profile.team_on
    game = task.task_group.game

    if not task.task_group.game.has_access('send_attempt', team=team):
        return NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))

    hint_number = int(request.POST['hint_number'])
    hint = get_object_or_404(Hint, task=task, number=hint_number)

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

    return {
        'status': 'ok',
        'task_id': task.id,
        'update_task_html': {
            task.id: render_task(task, request, team, current_mode)
        },
    }


@user_passes_test(has_team)
def send_hint_attempt(request, task_id):
    try:
        response = process_send_hint_attempt(request, task_id)
    except DuplicateAttemptException:
        response = {'status': 'duplicate'}
    except NotAllRequiredHintsTakenException:
        response = {'status': 'not_all_required_hints_taken'}
    return JsonResponse(response)


def task_ok_by_team(task, team, mode):
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


def results_page(request, game_id, mode='general'):
    game = get_object_or_404(Game, id=game_id)
    if has_profile(request.user) and request.user.profile.team_on and \
       not game.has_access('see_results', mode=mode, team=request.user.profile.team_on):
        return defaults.page_not_found(request)

    team_to_list_attempts_info = {}
    team_to_score = {}
    team_to_max_best_time = {}
    team_task_to_attempts_info = {}

    task_groups = sorted(game.task_groups.all(), key=lambda tg: tg.number)
    task_group_to_tasks = {}

    for task_group in task_groups:
        task_group_to_tasks[task_group.number] = sorted(
            task_group.tasks.filter(~Q(task_type='text_with_forms')) # исключаем задания этого типа из таблички
        , key=lambda t: t.key_sort())
        for task in task_group_to_tasks[task_group.number]:
            for attempts_info in Attempt.manager.get_task_attempts_infos(task=task, mode=mode):
                if attempts_info.attempts or attempts_info.hint_attempts:
                    if attempts_info.attempts:
                        team = attempts_info.attempts[0].team
                    else:
                        team = attempts_info.hint_attempts[0].team
                    if not team.is_hidden:
                        if team not in team_to_score:
                            team_to_score[team] = 0
                        task_points = 0
                        if attempts_info.best_attempt is not None:
                            task_points = attempts_info.best_attempt.points

                        if task_points > 0:
                            team_to_score[team] += max(0, task_points - attempts_info.get_sum_hint_penalty())
                            if team not in team_to_max_best_time:
                                team_to_max_best_time[team] = attempts_info.best_attempt.time
                            else:
                                team_to_max_best_time[team] = max(team_to_max_best_time[team], attempts_info.best_attempt.time)

                        team_task_to_attempts_info[(team, task)] = attempts_info

    for team in team_to_score.keys():
        for task_group in task_groups:
            for task in task_group_to_tasks[task_group.number]:
                if team not in team_to_list_attempts_info:
                    team_to_list_attempts_info[team] = []
                if (team, task) in team_task_to_attempts_info:
                    attempts_info = team_task_to_attempts_info[(team, task)]
                    team_to_list_attempts_info[team].append(attempts_info)
                else:
                    team_to_list_attempts_info[team].append(None)

    teams_sorted = []
    for team in team_to_score.keys():
        score = team_to_score[team]
        max_best_time = team_to_max_best_time.get(team, None)
        teams_sorted.append((-score, max_best_time, team))
    teams_sorted = [team for anti_score, max_best_time, team in sorted(
        teams_sorted,
        key=lambda t: (t[0], t[1], str(t[2]))
    )]

    return render(request, 'results.html', {
        'mode': mode,
        'game': game,
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'teams_sorted': teams_sorted,
        'team_to_list_attempts_info': team_to_list_attempts_info,
        'team_to_score': team_to_score,
        'team_to_max_best_time': team_to_max_best_time,
    })


def get_tournament_results(request, game_id):
    return results_page(request, game_id, mode='tournament')


# for game 29 :)
def return_intentional_503(request):
    return HttpResponse(status=503)
