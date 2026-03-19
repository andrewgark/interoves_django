"""Main UI: hub, games folder, profile, team."""
import datetime
import json
import logging
import os
import uuid
from collections import OrderedDict

import pytz
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.forms import ChoiceField, ModelForm, TextInput
from django.core.exceptions import ValidationError
from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.html import escape
from django.views.decorators.http import require_http_methods

from games.forms import CreateTeamForm, JoinTeamForm

from django.db.models import Count, Q
from django.utils import timezone

from allauth.socialaccount.models import SocialAccount

from games.access import game_has_started
from games.exception import NoGameAccessException
from games.models import Attempt, AudioManager, Game, HintAttempt, HTMLPage, ImageManager, Like, Profile, Project, Task, TaskGroup, Team, TicketRequest
from games.models import GameResultsSnapshot
from games.util import clean_text
from games.replacements_lines import parse_replacements_lines_text
from games.views.main_page import MainPageView
from games.views.util import has_profile, has_team
from games.results_snapshot import snapshot_to_results_context
from games.yookassa_util import configure_yookassa_from_env

from yookassa import Payment

logger = logging.getLogger(__name__)


def _ru_plural_form_int(n, one, few, many):
    n = abs(int(n))
    n_mod_100 = n % 100
    if 11 <= n_mod_100 <= 14:
        return many
    n_mod_10 = n % 10
    if n_mod_10 == 1:
        return one
    if 2 <= n_mod_10 <= 4:
        return few
    return many


def _ru_iz_punkt_word(n):
    # родительный падеж после "из N ..."
    return _ru_plural_form_int(n, 'пункта', 'пунктов', 'пунктов')


def _compute_solved_task_ids(game, task_groups, team=None, user=None, anon_key=None, mode='general'):
    """
    Returns:
      - solved_task_ids: set(task_id) solved by current actor
      - tg_to_task_ids: {task_group_id: [task_id, ...]} (for computing per-group stats)
    """
    tg_ids = [tg.id for tg in task_groups]
    tasks_qs = Task.objects.filter(task_group_id__in=tg_ids).values('id', 'task_group_id')
    task_ids = [t['id'] for t in tasks_qs]

    solved_task_ids = set()
    if task_ids:
        ok_attempts = Attempt.manager.filter(task_id__in=task_ids, status='Ok')
        if team is not None:
            ok_attempts = ok_attempts.filter(team=team, user__isnull=True, anon_key__isnull=True)
        elif user is not None:
            ok_attempts = ok_attempts.filter(user=user, team__isnull=True, anon_key__isnull=True)
        elif anon_key is not None:
            ok_attempts = ok_attempts.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
        else:
            ok_attempts = ok_attempts.none()
        # Approximate tournament filter similarly to sections page.
        if mode == 'tournament':
            ok_attempts = ok_attempts.filter(time__lte=game.end_time)
        solved_task_ids = set(ok_attempts.values_list('task_id', flat=True))

    tg_to_task_ids = {}
    for t in tasks_qs:
        tg_to_task_ids.setdefault(t['task_group_id'], []).append(t['id'])

    return solved_task_ids, tg_to_task_ids

NEW_UI_PROJECT = 'main'
NEW_UI_SECTIONS_PROJECT = 'sections'
PALINDROMES_GAME_ID = 'palindromes'
# Разделы с собственным туториалом (модалка правил)
SECTION_RULES_GAME_IDS = ('palindromes', 'replacements', 'walls')


def _session_play_mode_key(project_id):
    return 'play_mode_{}'.format(project_id or 'main')


def _default_play_mode(project_id):
    return 'personal' if project_id == NEW_UI_SECTIONS_PROJECT else 'team'


def _get_play_mode(request, project_id):
    key = _session_play_mode_key(project_id)
    mode = request.session.get(key)
    if mode not in ('team', 'personal'):
        mode = _default_play_mode(project_id)
    return mode, key

# Один общий раздел «Десяточки» (игры из project main); остальные — по одной игре из project sections.
NEW_UI_FOLDERS = [
    {
        'slug': 'games',
        'title': 'Десяточки',
        'description': 'Командные сложные игры, в которых можно пользоваться интернетом',
        'type': 'games',
    },
]


def get_section_games(request):
    """Игры из project 'sections' с доступом на превью (для плиток Разделов и навигации)."""
    project = Project.objects.filter(id=NEW_UI_SECTIONS_PROJECT).first()
    if not project:
        return []
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    games_list = [
        g for g in Game.objects.filter(project=project)
        if g.has_access('see_game_preview', team=team)
    ]
    return sorted(games_list, key=lambda g: (g.start_time, g.name), reverse=True)


def _folder_by_slug(slug):
    for f in NEW_UI_FOLDERS:
        if f['slug'] == slug:
            return f
    return None


def new_hub(request):
    section_games = get_section_games(request)
    return render(request, 'ui/hub.html', {
        'folders': NEW_UI_FOLDERS,
        'section_games': section_games,
        'page_title': 'Interoves',
    })


def new_folder(request, slug):
    folder = _folder_by_slug(slug)
    if not folder:
        raise Http404()
    if folder['type'] == 'games':
        return _new_folder_games(request)
    raise Http404()


def _new_folder_games(request):
    view = MainPageView()
    view.project_name = NEW_UI_PROJECT
    view.games_per_page = 20
    all_games = view.get_games_list(request)

    # AJAX pagination (same shape as old index smart-scrollbar)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        from django.core.paginator import Paginator
        page = int(request.GET.get('page', 1))
        paginator = Paginator(all_games, view.games_per_page)
        games_page = paginator.get_page(page)
        games_html = render(request, 'ui/games_list_items.html', {
            'games': games_page,
            'game_list_offset': (page - 1) * view.games_per_page,
        }).content.decode('utf-8')
        return JsonResponse({
            'games_html': games_html,
            'page': page,
            'has_next': games_page.has_next(),
            'total_pages': paginator.num_pages,
            'total_games': len(all_games),
        })

    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    return render(request, 'ui/folder_games.html', {
        'project': project,
        'games': all_games[:view.games_per_page],
        'total_games': len(all_games),
        'games_per_page': view.games_per_page,
        'page_title': 'Десяточки',
    })


def new_section_game_page(request, game_id):
    """Страница раздела (игра из project sections) в новом UI: правила при необходимости + список групп заданий."""
    project = Project.objects.filter(id=NEW_UI_SECTIONS_PROJECT).first()
    if not project:
        raise Http404()
    game = Game.objects.filter(project=project, id=game_id).first()
    if not game:
        raise Http404()
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    if not game.has_access('see_game_preview', team=team):
        raise Http404()
    task_groups = (
        TaskGroup.objects.filter(game=game)
        .annotate(n_tasks=Count('tasks'))
        .order_by('number')
    )
    play_mode, play_mode_key = _get_play_mode(request, game.project_id)

    # Для скрытия полностью решённых групп нужно быстро понять, какие задачи решены в текущем режиме.
    mode = game.get_current_mode(Attempt(time=timezone.now()))
    team = None
    user = None
    anon_key = None
    if play_mode == 'team':
        if has_profile(request.user):
            team = request.user.profile.team_on
    else:
        if request.user.is_authenticated and has_profile(request.user):
            user = request.user
        else:
            anon_key = request.GET.get('anon')

    solved_task_ids, tg_to_task_ids = _compute_solved_task_ids(
        game=game,
        task_groups=task_groups,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
    )

    task_group_rows = []
    for tg in task_groups:
        tg_task_ids = tg_to_task_ids.get(tg.id, [])
        n_solved = len([tid for tid in tg_task_ids if tid in solved_task_ids])
        is_fully_solved = bool(tg_task_ids) and n_solved >= len(tg_task_ids)
        row_class = ''
        if tg.n_tasks and n_solved >= tg.n_tasks:
            row_class = 'new-task--solved'
        elif n_solved:
            row_class = 'new-task--partial'

        is_fully_solved = bool(tg_task_ids) and all(
            tid in solved_task_ids for tid in tg_to_task_ids.get(tg.id, [])
        )
        task_group_rows.append({
            'task_group': tg,
            'game': game,
            'n_tasks': tg.n_tasks,
            'n_solved': n_solved,
            'play_url': '/games/{}/{}/'.format(game_id, tg.number),
            'is_fully_solved': is_fully_solved,
            'row_class': row_class,
            'title': tg.name,
            'progress_text': '{} из {} {} решено'.format(n_solved, tg.n_tasks, _ru_iz_punkt_word(tg.n_tasks)),
        })
    section_rules_type = game_id if game_id in SECTION_RULES_GAME_IDS else None
    section_tutorial_html = None
    if section_rules_type:
        try:
            page = HTMLPage.objects.get(name='section_tutorial_' + section_rules_type)
            section_tutorial_html = page.html or ''
        except HTMLPage.DoesNotExist:
            pass
    return render(request, 'ui/game_page.html', {
        'game': game,
        'task_group_rows': task_group_rows,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'page_title': game.outside_name or game.name,
        'show_palindrome_rules': game_id == PALINDROMES_GAME_ID,
        'section_rules_type': section_rules_type,
        'section_tutorial_html': section_tutorial_html,
        'is_main_game': False,
        'task_groups_heading': 'Наборы заданий',
        'task_groups_empty_text': 'В этом разделе пока нет групп заданий. Добавьте их в админке.',
        'back_url': '/',
    })


def new_main_game_page(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    if game.project_id != NEW_UI_PROJECT:
        raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    if not request.user.is_authenticated:
        play_mode = 'personal'
    anon_key = request.GET.get('anon') if not request.user.is_authenticated else None
    team = None
    user = request.user if request.user.is_authenticated else None
    has_profile_user = has_profile(request.user)
    if has_profile_user:
        team = request.user.profile.team_on

    # Page is accessible for preview; "Играть" button in list is shown only when access_play is true.
    if not game.has_access('see_game_preview', team=team):
        raise Http404()

    mode = game.get_current_mode(Attempt(time=timezone.now()))

    actor_label = 'Вы'
    actor_value = 'гость'
    if play_mode == 'team':
        if team:
            actor_value = 'команда {}'.format(team.visible_name)
        else:
            actor_value = 'команда'
    else:
        if has_profile(request.user):
            fn = (request.user.profile.first_name or '').strip()
            ln = (request.user.profile.last_name or '').strip()
            name = ('{} {}'.format(fn, ln)).strip()
            actor_value = name or request.user.get_username()
        elif request.user.is_authenticated:
            actor_value = request.user.get_username()

    task_groups = (
        TaskGroup.objects.filter(game=game)
        .annotate(n_tasks=Count('tasks'))
        .order_by('number')
    )

    solved_task_ids, tg_to_task_ids = _compute_solved_task_ids(
        game=game,
        task_groups=task_groups,
        team=team if play_mode == 'team' else None,
        user=user if play_mode != 'team' else None,
        anon_key=anon_key if play_mode != 'team' else None,
        mode=mode,
    )

    task_group_rows = []
    for tg in task_groups:
        n_solved = len([tid for tid in tg_to_task_ids.get(tg.id, []) if tid in solved_task_ids])
        row_class = ''
        if tg.n_tasks and n_solved >= tg.n_tasks:
            row_class = 'new-task--solved'
        elif n_solved:
            row_class = 'new-task--partial'
        task_group_rows.append({
            'task_group': tg,
            'n_tasks': tg.n_tasks,
            'n_solved': n_solved,
            'play_url': '/games/{}/{}/'.format(game.id, tg.number),
            'is_fully_solved': bool(tg.n_tasks) and n_solved >= tg.n_tasks,
            'row_class': row_class,
            'title': 'Группа {} · {}'.format(tg.number, tg.name),
            'progress_text': '{} из {} {} решено'.format(n_solved, tg.n_tasks, _ru_iz_punkt_word(tg.n_tasks)),
        })
    return render(request, 'ui/game_page.html', {
        'game': game,
        'task_group_rows': task_group_rows,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'current_mode': mode,
        'actor_label': actor_label,
        'actor_value': actor_value,
        'team': team,
        'has_profile_user': has_profile_user,
        'page_title': game.get_outside_name() if hasattr(game, 'get_outside_name') else (game.outside_name or game.name),
        'is_main_game': True,
        'task_groups_heading': 'Задания',
        'task_groups_empty_text': 'В этой игре пока нет групп заданий.',
        'back_url': '/games/',
    })


def _new_results_compute(game, mode):
    team_to_list_attempts_info = {}
    team_to_score = {}
    team_to_max_best_time = {}
    team_task_to_attempts_info = {}

    task_groups = sorted(game.task_groups.all(), key=lambda tg: tg.number)
    task_group_to_tasks = {}
    for task_group in task_groups:
        task_group_to_tasks[task_group.number] = sorted(
            task_group.tasks.filter(~Q(task_type='text_with_forms')),
            key=lambda t: t.key_sort()
        )
        for task in task_group_to_tasks[task_group.number]:
            for attempts_info in Attempt.manager.get_task_attempts_infos(task=task, mode=mode):
                if not (attempts_info.attempts or attempts_info.hint_attempts):
                    continue
                # Результаты в старом интерфейсе — командные. Сохраняем то же поведение.
                team = None
                if attempts_info.attempts:
                    team = attempts_info.attempts[0].team
                elif attempts_info.hint_attempts:
                    team = attempts_info.hint_attempts[0].team
                if not team or team.is_hidden:
                    continue

                if team not in team_to_score:
                    team_to_score[team] = 0

                task_points = 0
                if attempts_info.best_attempt is not None:
                    task_points = attempts_info.best_attempt.points
                if task_points and task_points > 0:
                    team_to_score[team] += max(0, task_points - attempts_info.get_sum_hint_penalty())
                    if team not in team_to_max_best_time:
                        team_to_max_best_time[team] = attempts_info.best_attempt.time
                    else:
                        team_to_max_best_time[team] = max(team_to_max_best_time[team], attempts_info.best_attempt.time)

                team_task_to_attempts_info[(team, task)] = attempts_info

    for team in team_to_score.keys():
        for task_group in task_groups:
            for task in task_group_to_tasks[task_group.number]:
                team_to_list_attempts_info.setdefault(team, [])
                team_to_list_attempts_info[team].append(team_task_to_attempts_info.get((team, task)))

    teams_sorted = []
    for team, score in team_to_score.items():
        max_best_time = team_to_max_best_time.get(team, datetime.datetime.now())
        teams_sorted.append((-score, max_best_time, team))
    teams_sorted = [team for anti_score, max_best_time, team in sorted(teams_sorted, key=lambda t: (t[0], t[1], str(t[2])))]

    team_to_place = {}
    for i, team in enumerate(teams_sorted):
        team_to_place[team] = 1 + i
        if i:
            prev_team = teams_sorted[i - 1]
            if team_to_score[team] == team_to_score[prev_team]:
                team_to_place[team] = team_to_place[prev_team]

    if mode == 'tournament':
        game.results = json.dumps({
            team.name: {'score': str(score), 'place': team_to_place[team]} for team, score in team_to_score.items()
        })
        game.save(update_fields=['results'])

    # Prepare per-cell metadata for templates: color by points vs max.
    tasks_flat = []
    for tg in task_groups:
        for task in task_group_to_tasks[tg.number]:
            tasks_flat.append(task)

    def _to_float(x):
        try:
            return float(x)
        except Exception:
            return 0.0

    team_to_cells = {}
    for team in teams_sorted:
        cells = []
        attempts_list = team_to_list_attempts_info.get(team, [])
        for idx, task in enumerate(tasks_flat):
            ai = attempts_list[idx] if idx < len(attempts_list) else None
            max_points = _to_float(getattr(task, 'get_points', None)() if hasattr(task, 'get_points') else getattr(task, 'points', 0))
            points = 0.0
            has_attempts = False
            if ai:
                try:
                    has_attempts = (ai.get_n_attempts() if callable(getattr(ai, 'get_n_attempts', None)) else ai.get_n_attempts) > 0
                except Exception:
                    has_attempts = False
                try:
                    points = _to_float(ai.get_result_points())
                except Exception:
                    points = 0.0

            cls = 'cell-no'
            if max_points > 0 and points >= max_points - 1e-9:
                cls = 'cell-full'
            elif points > 0:
                cls = 'cell-some'
            elif has_attempts:
                cls = 'cell-zero'

            cells.append({
                'ai': ai,
                'cls': cls,
            })
        team_to_cells[team] = cells

    return {
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'teams_sorted': teams_sorted,
        'team_to_list_attempts_info': team_to_list_attempts_info,
        'team_to_cells': team_to_cells,
        'team_to_score': team_to_score,
        'team_to_place': team_to_place,
        'team_to_max_best_time': team_to_max_best_time,
    }


def new_results_page(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    if game.project_id != NEW_UI_PROJECT:
        raise Http404()
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    # Results are viewable without being logged in; permissions are enforced by access rules.
    if not game.has_access('see_results', mode='general', team=team):
        raise Http404()

    snap = GameResultsSnapshot.objects.filter(game=game, mode='general').first()
    if snap and snap.payload:
        data = snapshot_to_results_context(game, snap.payload)
    else:
        data = _new_results_compute(game, mode='general')
    return render(request, 'ui/results.html', {
        'mode': 'general',
        'game': game,
        'team': team,
        'back_url': '/games/{}/'.format(game.id),
        **data,
        'play_mode': _get_play_mode(request, game.project_id)[0],
        'play_mode_project_id': game.project_id,
        'page_title': 'Результаты: {}'.format(game.get_no_html_name() if hasattr(game, 'get_no_html_name') else game.name),
    })


def new_tournament_results_page(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    if game.project_id != NEW_UI_PROJECT:
        raise Http404()
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    # Tournament results are viewable without being logged in; permissions are enforced by access rules.
    if not game.has_access('see_tournament_results', team=team):
        raise Http404()

    snap = GameResultsSnapshot.objects.filter(game=game, mode='tournament').first()
    if snap and snap.payload:
        data = snapshot_to_results_context(game, snap.payload)
    else:
        data = _new_results_compute(game, mode='tournament')
    return render(request, 'ui/results.html', {
        'mode': 'tournament',
        'game': game,
        'team': team,
        'back_url': '/games/{}/'.format(game.id),
        **data,
        'play_mode': _get_play_mode(request, game.project_id)[0],
        'play_mode_project_id': game.project_id,
        'page_title': 'Результаты турнира: {}'.format(game.get_no_html_name() if hasattr(game, 'get_no_html_name') else game.name),
    })


def new_task_group_page(request, game_id, task_group_number):
    game = get_object_or_404(Game, id=game_id)
    play_mode, play_mode_key = _get_play_mode(request, game.project_id)
    anon_key = None

    if not request.user.is_authenticated:
        # До логина разрешаем только личный режим.
        play_mode = 'personal'
        anon_key = request.GET.get('anon') or request.COOKIES.get('interoves_anon')  # fallback, основной — localStorage
    else:
        if play_mode == 'personal' and not has_profile(request.user):
            raise Http404()

    team = None
    user = None
    if play_mode == 'team':
        if not request.user.profile.team_on:
            raise Http404()
        team = request.user.profile.team_on if has_team(request.user) else None
    else:
        user = request.user if request.user.is_authenticated else None

    # Для игр-разделов (project sections) хотим давать доступ всегда, без привязки к start_time.
    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        preview_team = None
        if has_profile(request.user):
            preview_team = request.user.profile.team_on
        if not game.has_access('see_game_preview', team=preview_team):
            raise Http404()
    else:
        # Для "обычных" игр сохраняем старое поведение.
        if not game_has_started(game):
            raise Http404()
        if play_mode == 'team':
            if game.has_access('needs_registration', team=team) and not game.has_access('is_registered', team=team):
                raise Http404()
            if not game.has_access('play', team=team):
                raise NoGameAccessException('User {} has no access to game {}'.format(request.user.profile, game))
        else:
            if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
                raise Http404()
            if not game.is_playable:
                raise Http404()

    mode = game.get_current_mode(Attempt(time=timezone.now()))
    task_group = TaskGroup.objects.filter(game=game, number=task_group_number).first()
    if not task_group:
        # Если пользователь открыл несуществующий номер группы — перенаправим на ближайшую существующую.
        next_tg = TaskGroup.objects.filter(game=game, number__gt=task_group_number).order_by('number').first()
        prev_tg = TaskGroup.objects.filter(game=game, number__lt=task_group_number).order_by('-number').first()
        fallback = next_tg or prev_tg
        if fallback:
            return redirect('new_task_group', game_id=game.id, task_group_number=fallback.number)
        raise Http404()
    prev_tg = TaskGroup.objects.filter(game=game, number__lt=task_group.number).order_by('-number').first()
    next_tg = TaskGroup.objects.filter(game=game, number__gt=task_group.number).order_by('number').first()
    tasks = sorted(task_group.tasks.all(), key=lambda t: t.key_sort())
    attempts_info_by_task_id = {
        t.id: Attempt.manager.get_attempts_info(team=team, task=t, mode=mode, user=user, anon_key=anon_key)
        for t in tasks
    }
    wall_max_points_meta_by_task_id = {}
    for t in tasks:
        if t.task_type != 'wall':
            continue
        try:
            wall = t.get_wall()
            base_max = getattr(wall, 'max_points', None)
            if base_max is None:
                continue
            total = base_max * t.get_points()
            try:
                n_cat = int(getattr(wall, 'n_cat', 0))
                pw = int(getattr(wall, 'points_words', 0))
                pe = int(getattr(wall, 'points_explanation', 0))
                pb = int(getattr(wall, 'points_bonus', 0))
            except Exception:
                n_cat, pw, pe, pb = 0, 0, 0, 0
            base_parts_words = n_cat * pw
            base_parts_expl = n_cat * pe
            base = base_parts_words + base_parts_expl + pb
            mul = t.get_points()
            # total may be Decimal; show without trailing .000
            try:
                total_int = int(total)
                total_str = str(total_int) if total == total_int else str(total).rstrip('0').rstrip('.')
            except Exception:
                total_str = str(total).rstrip('0').rstrip('.')
            if mul == 1:
                title = '{total} = {w} за состав категорий + {e} за смысл категорий + {b} за полное решение'.format(
                    total=total_str,
                    w=base_parts_words,
                    e=base_parts_expl,
                    b=pb,
                )
            else:
                try:
                    mul_int = int(mul)
                    mul_str = str(mul_int) if mul == mul_int else str(mul).rstrip('0').rstrip('.')
                except Exception:
                    mul_str = str(mul).rstrip('0').rstrip('.')
                # Расписываем подробно: (w + e + b) × mul = w*mul + e*mul + b*mul
                try:
                    w_mul = base_parts_words * mul
                    e_mul = base_parts_expl * mul
                    b_mul = pb * mul
                    w_mul_int = int(w_mul)
                    e_mul_int = int(e_mul)
                    b_mul_int = int(b_mul)
                    w_mul_str = str(w_mul_int) if w_mul == w_mul_int else str(w_mul).rstrip('0').rstrip('.')
                    e_mul_str = str(e_mul_int) if e_mul == e_mul_int else str(e_mul).rstrip('0').rstrip('.')
                    b_mul_str = str(b_mul_int) if b_mul == b_mul_int else str(b_mul).rstrip('0').rstrip('.')
                except Exception:
                    w_mul_str = str(base_parts_words)
                    e_mul_str = str(base_parts_expl)
                    b_mul_str = str(pb)
                title = (
                    '{total} = ({w} за состав + {e} за смысл + {b} за бонус) × {mul} '
                    '= {w2} + {e2} + {b2}'
                ).format(
                    total=total_str,
                    w=base_parts_words,
                    e=base_parts_expl,
                    b=pb,
                    mul=mul_str,
                    w2=w_mul_str,
                    e2=e_mul_str,
                    b2=b_mul_str,
                )
            wall_max_points_meta_by_task_id[t.id] = {'total': total, 'title': title}
        except Exception:
            pass
    likes_meta_by_task_id = {}
    for t in tasks:
        likes_meta_by_task_id[t.id] = {
            # Показываем сумму КОМАНДНЫХ + ЛИЧНЫХ лайков/дизлайков.
            'likes': Like.manager.get_total_likes(t),
            'dislikes': Like.manager.get_total_dislikes(t),
            # Лайк/дизлайк ставим в зависимости от режима.
            'liked': Like.manager.actor_has_like(t, team=team, user=user, anon_key=anon_key),
            'disliked': Like.manager.actor_has_dislike(t, team=team, user=user, anon_key=anon_key),
        }
    section_rules_type = game.id if game.id in SECTION_RULES_GAME_IDS else None
    section_tutorial_html = None
    if section_rules_type:
        try:
            page = HTMLPage.objects.get(name='section_tutorial_' + section_rules_type)
            section_tutorial_html = page.html or ''
        except HTMLPage.DoesNotExist:
            pass
    replacements_lines_data = {}
    for t in tasks:
        if t.task_type == 'replacements_lines':
            parsed = parse_replacements_lines_text(t.text, (t.checker_data or '').strip() or None)
            n_lines = len(parsed['left_lines'])
            line_solved = [False] * n_lines
            line_attempts = [0] * n_lines
            answers_by_line = parsed.get('answers', [])
            slot_correct = [
                [False] * len(answers_by_line[i]) for i in range(n_lines)
            ]
            line_done = [False] * n_lines
            solved_lines_from_state = set()
            ai = attempts_info_by_task_id.get(t.id)
            if ai and ai.attempts:
                for a in ai.attempts:
                    try:
                        p = json.loads(a.text)
                        idx = int(p.get('line_index', -1))
                        if 0 <= idx < n_lines:
                            line_attempts[idx] += 1
                            user_answers = p.get('answers', []) or []
                            correct_answers = answers_by_line[idx] if idx < len(answers_by_line) else []
                            for j in range(min(len(user_answers), len(correct_answers))):
                                if clean_text(user_answers[j]) == clean_text(correct_answers[j]):
                                    slot_correct[idx][j] = True
                    except (ValueError, TypeError):
                        pass
                    # Состояние накопительных очков/решённых строк хранится в a.state
                    if a.state:
                        try:
                            st = json.loads(a.state)
                            solved_lines_from_state = set(st.get('solved_lines', []) or [])
                        except (ValueError, TypeError):
                            pass
            for i in range(n_lines):
                if i in solved_lines_from_state:
                    line_done[i] = True
            for i in range(n_lines):
                # Строка считается завершённой, если либо была попытка Ok,
                # либо уже все слоты совпали (по накопленным slot_correct).
                if not line_done[i]:
                    # fallback для старых данных без state: если все слоты совпали по компонентам
                    line_done[i] = bool(slot_correct[i]) and all(slot_correct[i])
            replacements_lines_data[t.id] = {
                'parsed': parsed,
                'line_solved': line_solved,
                'line_done': line_done,
                'line_attempts': line_attempts,
                'slot_correct': slot_correct,
                'n_lines': n_lines,
                'max_attempts': t.get_max_attempts(),
                'max_points_total': t.get_points() * n_lines,
            }
    return render(request, 'ui/task_group.html', {
        'game': game,
        'task_group': task_group,
        'tasks': tasks,
        'attempts_info_by_task_id': attempts_info_by_task_id,
        'replacements_lines_data': replacements_lines_data,
        'wall_max_points_meta_by_task_id': wall_max_points_meta_by_task_id,
        'likes_meta_by_task_id': likes_meta_by_task_id,
        'can_like': True,
        'has_profile_user': has_profile(request.user),
        'mode': mode,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'anon_key': anon_key,
        'team': team,
        'show_palindrome_rules': game.id == PALINDROMES_GAME_ID,
        'section_rules_type': section_rules_type,
        'section_tutorial_html': section_tutorial_html,
        'prev_task_group_url': '/games/{}/{}/'.format(game.id, prev_tg.number) if prev_tg else None,
        'next_task_group_url': '/games/{}/{}/'.format(game.id, next_tg.number) if next_tg else None,
        'back_url': (
            '/section/{}/'.format(game.id)
            if game.project_id == NEW_UI_SECTIONS_PROJECT
            else (
                '/games/{}/'.format(game.id)
                if game.project_id == NEW_UI_PROJECT
                else '/'
            )
        ),
        'page_title': '{} · {}'.format(game.outside_name or game.name, task_group.name),
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
    })


@require_http_methods(['GET'])
def new_get_answer(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    game = task.task_group.game

    play_mode, _ = _get_play_mode(request, game.project_id)
    team = None
    user = None
    anon_key = None
    if play_mode == 'team':
        if not has_profile(request.user) or not request.user.profile.team_on:
            raise Http404()
        team = request.user.profile.team_on
        if not game.has_access('play', team=team):
            raise Http404()
    else:
        if request.user.is_authenticated:
            if not has_profile(request.user):
                raise Http404()
            user = request.user
        else:
            anon_key = request.GET.get('anon') or request.GET.get('anon_key')
            if not anon_key:
                raise Http404()
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise Http404()

    mode = game.get_current_mode(Attempt(time=timezone.now()))
    attempts_info = Attempt.manager.get_attempts_info(team=team, user=user, anon_key=anon_key, task=task, mode=mode)
    if mode != 'general' and not attempts_info.is_solved():
        return JsonResponse({'html': '<div class="new-login-hint">Ответ доступен после верного решения.</div>'})

    html = '<div style="font-weight:700">{}</div>'.format(task.answer or '')
    return JsonResponse({'html': html})


@require_http_methods(['GET'])
def new_get_replacements_line_answer(request, task_id, line_index):
    task = get_object_or_404(Task, id=task_id)
    game = task.task_group.game

    play_mode, _ = _get_play_mode(request, game.project_id)
    team = None
    user = None
    anon_key = None
    if play_mode == 'team':
        if not has_profile(request.user) or not request.user.profile.team_on:
            raise Http404()
        team = request.user.profile.team_on
        if not game.has_access('play', team=team):
            raise Http404()
    else:
        if request.user.is_authenticated:
            if not has_profile(request.user):
                raise Http404()
            user = request.user
        else:
            anon_key = request.GET.get('anon') or request.GET.get('anon_key')
            if not anon_key:
                raise Http404()
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise Http404()

    mode = game.get_current_mode(Attempt(time=timezone.now()))
    attempts_info = Attempt.manager.get_attempts_info(team=team, user=user, anon_key=anon_key, task=task, mode=mode)
    if mode != 'general' and not attempts_info.is_solved():
        return JsonResponse({'html': '<div class="new-login-hint">Ответ доступен после верного решения.</div>'})

    # Для replacements_lines ответы живут в checker_data (output-текст).
    lines = (task.checker_data or '').splitlines()
    try:
        text = lines[int(line_index)]
    except Exception:
        text = ''
    if not text.strip():
        return JsonResponse({'html': '<div class="new-login-hint">Нет ответа.</div>'})
    return JsonResponse({'html': '<div style="font-weight:700">{}</div>'.format(escape(text))})


@require_http_methods(['POST'])
def new_like_dislike(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    game = task.task_group.game

    play_mode, _ = _get_play_mode(request, game.project_id)
    team = None
    user = None
    anon_key = None
    if play_mode == 'team':
        if not has_profile(request.user) or not request.user.profile.team_on:
            raise Http404()
        team = request.user.profile.team_on
        if not game.has_access('send_attempt', team=team):
            raise Http404()
    else:
        if request.user.is_authenticated:
            user = request.user
        else:
            anon_key = request.POST.get('anon_key')
            if not anon_key:
                raise Http404()
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise Http404()

    likes = int(request.POST.get('likes', 0))
    dislikes = int(request.POST.get('dislikes', 0))
    if likes == 1:
        # make reactions mutually exclusive for this actor
        Like.manager.delete_dislike_actor(task, team=team, user=user, anon_key=anon_key)
        Like.manager.add_like_actor(task, team=team, user=user, anon_key=anon_key)
    elif likes == -1:
        Like.manager.delete_like_actor(task, team=team, user=user, anon_key=anon_key)
    if dislikes == 1:
        Like.manager.delete_like_actor(task, team=team, user=user, anon_key=anon_key)
        Like.manager.add_dislike_actor(task, team=team, user=user, anon_key=anon_key)
    elif dislikes == -1:
        Like.manager.delete_dislike_actor(task, team=team, user=user, anon_key=anon_key)

    return JsonResponse({
        # показываем сумму КОМАНДНЫХ + ЛИЧНЫХ лайков/дизлайков
        'likes': Like.manager.get_total_likes(task),
        'dislikes': Like.manager.get_total_dislikes(task),
        # а состояние — текущего режима
        'liked': Like.manager.actor_has_like(task, team=team, user=user, anon_key=anon_key),
        'disliked': Like.manager.actor_has_dislike(task, team=team, user=user, anon_key=anon_key),
    })


@require_http_methods(['GET'])
def new_set_play_mode(request):
    mode = request.GET.get('mode')
    if mode in ('team', 'personal'):
        project_id = request.GET.get('project') or NEW_UI_PROJECT
        request.session[_session_play_mode_key(project_id)] = mode
    next_url = request.GET.get('next') or '/'
    return redirect(next_url)


@login_required
@require_http_methods(['POST'])
def new_migrate_anon_attempts(request):
    if not has_profile(request.user):
        raise Http404()
    anon_key = request.POST.get('anon_key')
    if not anon_key:
        raise Http404()
    moved = Attempt.objects.filter(anon_key=anon_key, user__isnull=True, team__isnull=True).update(
        user=request.user,
        anon_key=None,
    )
    moved_hints = HintAttempt.objects.filter(anon_key=anon_key, user__isnull=True, team__isnull=True).update(
        user=request.user,
        anon_key=None,
    )
    return JsonResponse({'status': 'ok', 'moved': moved, 'moved_hints': moved_hints})


class ProfileSettingsForm(ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].widget.attrs.update({'placeholder': 'Имя'})
        self.fields['last_name'].widget.attrs.update({'placeholder': 'Фамилия'})
        # keep model field, but render as text input with datalist
        self.fields['timezone'].widget = TextInput()
        self.fields['timezone'].required = True
        self.fields['timezone'].widget.attrs.update({
            'list': 'tz-list',
            'placeholder': 'Europe/Moscow (UTC+03:00)',
            'autocomplete': 'off',
        })
        if getattr(self.instance, 'timezone', None):
            # show with UTC offset (datalist in Chrome uses option.value only)
            try:
                tz_name = self.instance.timezone
                tz_obj = pytz.timezone(tz_name)
                utc_now = datetime.datetime.utcnow().replace(tzinfo=pytz.UTC)
                off = utc_now.astimezone(tz_obj).utcoffset()
                if off is not None:
                    total = int(off.total_seconds())
                    sign = '+' if total >= 0 else '-'
                    total = abs(total)
                    hh = total // 3600
                    mm = (total % 3600) // 60
                    self.initial['timezone'] = '{} (UTC{}{:02d}:{:02d})'.format(tz_name, sign, hh, mm)
                else:
                    self.initial['timezone'] = tz_name
            except Exception:
                self.initial['timezone'] = self.instance.timezone

    class Meta:
        model = Profile
        fields = ['first_name', 'last_name', 'avatar_url', 'timezone']
        widgets = {
            'first_name': TextInput(),
            'last_name': TextInput(),
            'avatar_url': TextInput(),
        }

    def clean_timezone(self):
        tz = (self.cleaned_data.get('timezone') or '').strip()
        # Allow values like "Europe/Moscow (UTC+03:00)" (Chrome datalist shows only value).
        if ' (UTC' in tz and tz.endswith(')'):
            tz = tz.split(' (UTC', 1)[0].strip()
        if not tz:
            raise ValidationError('Выберите таймзону.')
        if tz not in pytz.common_timezones and tz not in pytz.all_timezones:
            raise ValidationError('Неизвестная таймзона.')
        return tz


@login_required
@require_http_methods(['GET', 'POST'])
def new_profile(request):
    if not has_profile(request.user):
        messages.error(request, 'Профиль недоступен.')
        return redirect('new_hub')
    profile = request.user.profile
    connected = set(SocialAccount.objects.filter(user=request.user).values_list('provider', flat=True))
    if request.method == 'POST':
        form = ProfileSettingsForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Профиль сохранён.')
            return redirect('new_profile')
    else:
        form = ProfileSettingsForm(instance=profile)
    def _utc_offset_label(tz_name):
        try:
            tz = pytz.timezone(tz_name)
            utc_now = datetime.datetime.utcnow().replace(tzinfo=pytz.UTC)
            off = utc_now.astimezone(tz).utcoffset()
            if off is None:
                return tz_name
            total = int(off.total_seconds())
            sign = '+' if total >= 0 else '-'
            total = abs(total)
            hh = total // 3600
            mm = (total % 3600) // 60
            return '{} (UTC{}{:02d}:{:02d})'.format(tz_name, sign, hh, mm)
        except Exception:
            return tz_name

    tz_options = [(tz, _utc_offset_label(tz)) for tz in pytz.common_timezones]

    return render(request, 'ui/profile.html', {
        'form': form,
        'connected_providers': connected,
        'tz_options': tz_options,
        'page_title': 'Профиль',
    })


@login_required
@require_http_methods(['GET'])
def new_team(request):
    if not has_profile(request.user):
        messages.error(request, 'Сначала войдите и создайте профиль.')
        return redirect('new_hub')
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    back = request.build_absolute_uri('/team/')
    teams = sorted(Team.objects.filter(project=project, is_hidden=False), key=lambda t: t.visible_name)
    return render(request, 'ui/team.html', {
        'project': project,
        'teams': teams,
        'page_title': 'Команда',
        'new_team_url': back,
    })


@login_required
@require_http_methods(['GET'])
def new_pay_page(request):
    """Новая страница оплаты: билеты команде (как /tickets/) + Interoves+ (пока скоро)."""
    if not has_profile(request.user):
        messages.error(request, 'Сначала войдите и создайте профиль.')
        return redirect('new_hub')
    team = request.user.profile.team_on
    recent_requests = []
    if team:
        recent_requests = list(TicketRequest.objects.filter(team=team).order_by('-time')[:20])
    raw_price = getattr(team, 'ticket_price', 2000) if team else 2000
    try:
        ticket_price_int = int(raw_price)
    except (TypeError, ValueError):
        ticket_price_int = 2000
    return render(request, 'ui/pay.html', {
        'team': team,
        'ticket_price': ticket_price_int,
        'show_school_discount_hint': ticket_price_int == 2000,
        'team_tickets': team.tickets if team else 0,
        'recent_ticket_requests': recent_requests,
        'page_title': 'Оплата',
    })


@require_http_methods(['POST'])
def new_create_ticket_payment(request):
    """
    Called via fetch() from /new/pay/ — must always return JSON so the client can r.json().
    (Redirects/HTML from @login_required or redirect() break fetch and show «Ошибка сети».)
    """
    if not request.user.is_authenticated:
        return JsonResponse(
            {'status': 'error', 'reason': 'login', 'message': 'Сессия истекла. Войдите снова и повторите оплату.'},
            status=401,
        )
    if not has_profile(request.user):
        return JsonResponse(
            {'status': 'error', 'reason': 'profile', 'message': 'Сначала войдите и создайте профиль.'},
            status=403,
        )
    if not has_team(request.user):
        return JsonResponse(
            {'status': 'error', 'reason': 'team', 'message': 'Нужно создать или вступить в команду, чтобы купить билет.'},
            status=403,
        )

    team = request.user.profile.team_on
    try:
        tickets = int((request.POST.get('tickets') or '').strip())
    except Exception:
        tickets = 0
    if tickets < 1 or tickets > 20:
        return JsonResponse(
            {'status': 'error', 'reason': 'tickets', 'message': 'Введите число билетов от 1 до 20.'},
            status=400,
        )

    ticket_price = int(getattr(team, 'ticket_price', 2000) or 2000)
    amount_rub = int(tickets * ticket_price)

    ticket_request = None
    try:
        ticket_request = TicketRequest.objects.create(
            team=team,
            money=amount_rub,
            tickets=tickets,
            status='Pending',
        )

        # YooKassa: description max 128 characters
        team_label = (getattr(team, 'visible_name', None) or getattr(team, 'name', None) or str(team.pk))
        payment_description = f'Билеты для команды {team_label} (request {ticket_request.id})'
        payment_description = payment_description[:128]

        configure_yookassa_from_env()
        payment = Payment.create({
            'amount': {
                'value': f'{amount_rub:.2f}',
                'currency': 'RUB',
            },
            'confirmation': {
                'type': 'embedded',
            },
            'capture': True,
            'description': payment_description,
            'metadata': {
                'ticket_request_id': str(ticket_request.id),
                'team_id': str(team.pk),
                'tickets': str(tickets),
                'kind': 'team_ticket',
            },
        }, uuid.uuid4().hex)
        payment_data = dict(payment)
        ticket_request.yookassa_id = payment_data.get('id') or ticket_request.yookassa_id
        ticket_request.save(update_fields=['yookassa_id'])
        confirmation_token = (payment_data.get('confirmation') or {}).get('confirmation_token')
        if not confirmation_token:
            raise RuntimeError('Missing confirmation_token from YooKassa')
    except RuntimeError as exc:
        if ticket_request is not None and 'Missing YooKassa credentials' in str(exc):
            logger.error('new_create_ticket_payment: %s', exc)
        else:
            logger.exception(
                'new_create_ticket_payment failed ticket_request_id=%s team_id=%s amount_rub=%s',
                getattr(ticket_request, 'id', None),
                team.pk,
                amount_rub,
            )
        if 'Missing YooKassa credentials' in str(exc):
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'yookassa_config',
                    'message': 'Оплата не настроена на сервере (ключи YooKassa). Обратитесь к администратору.',
                },
                status=503,
            )
        if ticket_request is None:
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'order',
                    'message': 'Не удалось создать заказ. Попробуйте позже.',
                },
                status=500,
            )
        return JsonResponse(
            {'status': 'error', 'reason': 'yookassa', 'message': 'Не получилось создать платёж. Попробуйте позже.'},
            status=502,
        )
    except Exception:
        logger.exception(
            'new_create_ticket_payment failed ticket_request_id=%s team_id=%s amount_rub=%s',
            getattr(ticket_request, 'id', None),
            team.pk,
            amount_rub,
        )
        if ticket_request is None:
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'db',
                    'message': 'Не удалось сохранить заказ (база данных). Попробуйте позже.',
                },
                status=500,
            )
        return JsonResponse(
            {'status': 'error', 'reason': 'yookassa', 'message': 'Не получилось создать платёж. Попробуйте позже.'},
            status=502,
        )

    return JsonResponse({
        'status': 'ok',
        'confirmation_token': confirmation_token,
        'return_url': request.build_absolute_uri('/pay/?payment=return'),
    })


@login_required
@require_http_methods(['GET'])
def new_team_name_check(request):
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    name = (request.GET.get('name') or '').strip()
    if not name:
        return JsonResponse({'ok': True, 'available': False, 'reason': 'empty'})
    exists = Team.objects.filter(project=project, name=name).exists()
    return JsonResponse({'ok': True, 'available': not exists})


@login_required
@require_http_methods(['GET'])
def new_team_info(request):
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    name = (request.GET.get('name') or '').strip()
    team = Team.objects.filter(project=project, name=name).first()
    if not team:
        team = Team.objects.filter(project=project, visible_name__iexact=name).first()
    if not team:
        return JsonResponse({'ok': True, 'exists': False})
    return JsonResponse({'ok': True, 'exists': True, 'n_users': team.get_n_users_on(), 'visible_name': team.visible_name})


@login_required
@require_http_methods(['POST'])
def new_team_create(request):
    if not has_profile(request.user) or request.user.profile.team_on:
        raise Http404()
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    name = (request.POST.get('name') or '').strip()
    if not name:
        raise Http404()
    if Team.objects.filter(project=project, name=name).exists():
        raise Http404()
    referer_name = (request.POST.get('referer') or '').strip()
    referer = None
    if referer_name:
        referer = Team.objects.filter(project=project, name=referer_name).first()
    team = Team(name=name, project=project, referer=referer)
    team.save()
    request.user.profile.team_on = team
    request.user.profile.team_requested = None
    request.user.profile.save()
    return redirect('new_team')


@login_required
@require_http_methods(['POST'])
def new_team_request_join(request):
    if not has_profile(request.user) or request.user.profile.team_on or request.user.profile.team_requested:
        raise Http404()
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    name = (request.POST.get('name') or '').strip()
    team = Team.objects.filter(project=project, name=name).first()
    if not team:
        team = Team.objects.filter(project=project, visible_name__iexact=name).first()
    if not team:
        raise Http404()
    request.user.profile.team_requested = team
    request.user.profile.save()
    return redirect('new_team')


@login_required
@require_http_methods(['POST'])
def new_team_join_by_password(request):
    if not has_profile(request.user) or request.user.profile.team_on:
        messages.error(request, 'Нельзя вступить в команду сейчас.')
        return redirect('new_team')
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    name = (request.POST.get('name') or '').strip()
    password = (request.POST.get('password') or '').strip()
    team = Team.objects.filter(project=project, name=name).first()
    if not team:
        team = Team.objects.filter(project=project, visible_name__iexact=name).first()
    if not team:
        messages.error(request, 'Команда не найдена.')
        return redirect('new_team')
    if not password or team.join_password != password:
        messages.error(request, 'Неверный пароль.')
        return redirect('new_team')
    request.user.profile.team_on = team
    request.user.profile.team_requested = None
    request.user.profile.save()
    messages.success(request, 'Вы вступили в команду.')
    return redirect('new_team')


@login_required
@require_http_methods(['GET', 'POST'])
def new_team_password(request):
    if not has_profile(request.user) or not request.user.profile.team_on:
        raise Http404()
    team = request.user.profile.team_on
    if request.method == 'GET':
        return JsonResponse({'ok': True, 'password': team.join_password or ''})
    password = (request.POST.get('password') or '').strip()
    if not password:
        raise Http404()
    team.join_password = password
    team.save(update_fields=['join_password'])
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['POST'])
def new_team_rename(request):
    if not has_profile(request.user) or not request.user.profile.team_on:
        raise Http404()
    team = request.user.profile.team_on
    visible_name = (request.POST.get('visible_name') or '').strip()
    if not visible_name:
        messages.error(request, 'Название не может быть пустым.')
        return redirect('new_team')
    team.visible_name = visible_name
    team.save(update_fields=['visible_name'])
    messages.success(request, 'Название команды обновлено.')
    return redirect('new_team')
