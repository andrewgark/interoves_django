"""Main UI: hub, games folder, profile, team."""
import datetime
import hmac
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
from django.core.paginator import Paginator
from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_http_methods

from games.forms import CreateTeamForm, JoinTeamForm

from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from allauth.socialaccount.models import SocialAccount

from games.access import game_has_started
from games.exception import NoGameAccessException
from games.models import (
    Attempt,
    AudioManager,
    Game,
    GameTaskGroup,
    HintAttempt,
    HTMLPage,
    ImageManager,
    Like,
    PersonalResultsParticipant,
    Profile,
    ProfileTeamMembership,
    Project,
    Task,
    TaskGroup,
    Team,
    TicketRequest,
)
from games.models import GameResultsSnapshot
from games.util import clean_text
from games.replacements_lines import canonical_replacements_checker_line, parse_replacements_lines_text
from games.proportions import build_proportions_chips_for_tasks
from games.views.game_context import game_from_request_for_task
from games.views.main_page import MainPageView
from games.views.util import (
    effective_play_mode,
    get_public_task_or_404,
    has_profile,
    has_team,
    personal_play_mode_locked,
)
from games.results_snapshot import snapshot_to_results_context
from games.yookassa_util import configure_yookassa_from_env

from yookassa import Payment

logger = logging.getLogger(__name__)


def _anon_key_from_request(request):
    """Идентификатор анонимного игрока в личном режиме: ?anon= / ?anon_key= или cookie interoves_anon (ставится JS в base)."""
    if request.user.is_authenticated:
        return None
    return (
        request.GET.get('anon')
        or request.GET.get('anon_key')
        or request.COOKIES.get('interoves_anon')
    )


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


class _ResultsTaskGroupHeader:
    """Заголовок столбца результатов: номер/название из GameTaskGroup."""

    __slots__ = ('number', 'name', '_n_tasks')

    def __init__(self, number, name, n_tasks):
        self.number = number
        self.name = name
        self._n_tasks = n_tasks

    def get_n_tasks_for_results(self):
        return self._n_tasks


def _compute_solved_task_ids(game, task_groups, team=None, user=None, anon_key=None, mode='general'):
    """
    Returns:
      - solved_task_ids: set(task_id) solved by current actor
      - tg_to_task_ids: {task_group_id: [task_id, ...]} (for computing per-group stats)
    """
    from games.scoring import Actor, bulk_actor_solved_task_ids

    tg_ids = [tg.id for tg in task_groups]
    tasks_qs = Task.objects.filter(task_group_id__in=tg_ids).visible().values('id', 'task_group_id')
    task_ids = [t['id'] for t in tasks_qs]

    solved_task_ids = set()
    if task_ids:
        actor = None
        if team is not None:
            actor = Actor(team_id=team.pk)
        elif user is not None:
            actor = Actor(user_id=user.pk)
        elif anon_key is not None:
            actor = Actor(anon_key=str(anon_key))
        if actor is not None:
            # For "sections" (training) we treat a task solved if it was solved in ANY game
            # that references the same canonical TaskGroup (same Task rows, different Game).
            include_other_games = game.project_id == NEW_UI_SECTIONS_PROJECT
            solved_task_ids = bulk_actor_solved_task_ids(
                tasks=Task.objects.filter(id__in=task_ids).visible(),
                actor=actor,
                mode=mode,
                game=game,
                include_other_games=include_other_games,
            )

    tg_to_task_ids = {}
    for t in tasks_qs:
        tg_to_task_ids.setdefault(t['task_group_id'], []).append(t['id'])

    return solved_task_ids, tg_to_task_ids

NEW_UI_PROJECT = 'main'
NEW_UI_SECTIONS_PROJECT = 'sections'
PALINDROMES_GAME_ID = 'palindromes'
# Разделы с собственным туториалом (модалка правил)
SECTION_RULES_GAME_IDS = ('palindromes', 'replacements', 'walls')


def _project_base(project_id: str | None) -> str:
    """
    URL base prefix for project-scoped UI.

    - main project lives at site root -> ""
    - other projects live under "/<project_id>" -> "/glowbyte"
    """
    pid = (project_id or '').strip()
    if not pid or pid == NEW_UI_PROJECT:
        return ''
    # Project ids in this repo are simple slugs, but keep it defensive.
    if '/' in pid:
        pid = pid.replace('/', '')
    return '/' + pid


def _project_urls_context(project_id: str | None):
    """
    Common URLs for templates to keep navigation inside current project scope.
    """
    base = _project_base(project_id)
    return {
        'ui_project_id': project_id or NEW_UI_PROJECT,
        'ui_project_base': base,  # no trailing slash
        'ui_project_home_url': (base + '/') or '/',
        'ui_project_games_url': (base + '/games/') if base else '/games/',
        'ui_project_team_url': (base + '/team/') if base else '/team/',
        'ui_project_profile_url': (base + '/profile/') if base else '/profile/',
        'ui_project_pay_url': (base + '/pay/') if base else '/pay/',
    }


def _scoped_project_id(request) -> str | None:
    """Non-main project id when current URL is under /<project_id>/… (e.g. glowbyte)."""
    match = getattr(request, 'resolver_match', None)
    if not match:
        return None
    pid = (match.kwargs or {}).get('project_id')
    if not pid:
        return None
    pid = str(pid).strip()
    return pid or None


def _main_team_page_urls():
    return {
        'ui_team_url_hub': reverse('new_team'),
        'ui_team_url_create': reverse('new_team_create'),
        'ui_team_url_join': reverse('new_team_join_page'),
        'ui_team_url_name_check': reverse('new_team_name_check'),
        'ui_team_url_info': reverse('new_team_info'),
        'ui_team_url_request_join': reverse('new_team_request_join'),
        'ui_team_url_join_by_password': reverse('new_team_join_by_password'),
        'ui_team_url_password': reverse('new_team_password'),
        'ui_team_url_rename': reverse('new_team_rename'),
        'ui_team_url_set_primary': reverse('new_team_set_primary'),
    }


def _project_team_page_urls(project_id: str):
    k = {'project_id': project_id}
    return {
        'ui_team_url_hub': reverse('project_team', kwargs=k),
        'ui_team_url_create': reverse('project_team_create', kwargs=k),
        'ui_team_url_join': reverse('project_team_join_page', kwargs=k),
        'ui_team_url_name_check': reverse('project_team_name_check', kwargs=k),
        'ui_team_url_info': reverse('project_team_info', kwargs=k),
        'ui_team_url_request_join': reverse('project_team_request_join', kwargs=k),
        'ui_team_url_join_by_password': reverse('project_team_join_by_password', kwargs=k),
        'ui_team_url_password': reverse('project_team_password', kwargs=k),
        'ui_team_url_rename': reverse('project_team_rename', kwargs=k),
        'ui_team_url_set_primary': reverse('project_team_set_primary', kwargs=k),
    }


def _merge_nav_project_for_scope(ctx: dict, request, scoped_id: str | None):
    """Header logo: scoped project; team hub still uses main project in `project` for tickets/teams DB."""
    if not scoped_id:
        return ctx
    nav_project = get_object_or_404(Project, id=scoped_id)
    ctx['nav_project'] = nav_project
    ctx.update(_project_urls_context(scoped_id))
    return ctx


def _profile_redirect(request):
    scoped = _scoped_project_id(request)
    if scoped:
        return redirect('project_profile', project_id=scoped)
    return redirect('new_profile')


def _team_redirect(request):
    scoped = _scoped_project_id(request)
    if scoped:
        return redirect('project_team', project_id=scoped)
    return redirect('new_team')


def _team_join_redirect(request):
    scoped = _scoped_project_id(request)
    if scoped:
        return redirect('project_team_join_page', project_id=scoped)
    return redirect('new_team_join_page')


def _section_task_groups_rules_modal_html(task_groups):
    """
    HTML для модалки «Правила» на странице раздела: непустые rules у канонических TaskGroup.
    Если есть хотя бы один блок — возвращаем разметку; иначе None (тогда остаются туториалы/хардкод).
    """
    parts = []
    for i, p in enumerate(task_groups):
        tg = p.task_group
        rules = getattr(tg, 'rules', None)
        if not rules:
            continue
        html = (rules.html or '').strip()
        if not html:
            continue
        margin = 'margin-top:0' if i == 0 else 'margin-top:1.25rem'
        heading = format_html(
            '<h3 class="new-heading" style="font-size:1.1rem;{}">{} · {}</h3>',
            margin,
            p.number,
            p.name,
        )
        parts.append(format_html('<div class="new-section-rules-block">{}</div>', heading + mark_safe(html)))
    if not parts:
        return None
    return mark_safe(''.join(str(x) for x in parts))


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
    project = Project.objects.filter(id=NEW_UI_PROJECT).first()
    section_games = get_section_games(request)
    return render(request, 'ui/hub.html', {
        'project': project,
        'folders': NEW_UI_FOLDERS,
        'section_games': section_games,
        'page_title': 'Interoves',
        'show_sections_nav': True,
        'community_links': [
            {'kind': 'telegram', 'title': 'Телеграм-канал', 'href': 'https://t.me/interoves'},
            {'kind': 'telegram', 'title': 'Чат участников', 'href': 'https://t.me/+rhsbkEuU4-ExOWEy'},
            {'kind': 'telegram', 'title': 'Чат решающих PuzzleHunts', 'href': 'https://t.me/+GPR22w8MdLEyNzIy'},
            {'kind': 'telegram', 'title': 'Разработчик: Андрей', 'href': 'https://t.me/andrewgark'},
            {'kind': 'vpn', 'title': 'VPN от наших друзей', 'href': '/vpn/'},
        ],
        **_project_urls_context(NEW_UI_PROJECT),
    })


def new_folder(request, slug):
    folder = _folder_by_slug(slug)
    if not folder:
        raise Http404()
    if folder['type'] == 'games':
        return _new_folder_games(request)
    raise Http404()


def _games_list_card_context(request):
    """Контекст для new/games_list_items: безопасно для анонима, team согласован с has_team."""
    if not request.user.is_authenticated or not has_profile(request.user):
        return {'games_card_team': None, 'games_card_has_team': False}
    has_t = has_team(request.user)
    return {
        'games_card_team': request.user.profile.team_on if has_t else None,
        'games_card_has_team': has_t,
    }


def _new_folder_games(request):
    view = MainPageView()
    view.project_name = NEW_UI_PROJECT
    view.games_per_page = 20
    all_games = view.get_games_list(request)
    card_ctx = _games_list_card_context(request)

    # AJAX pagination (append cards on window scroll near bottom)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        from django.core.paginator import Paginator
        page = int(request.GET.get('page', 1))
        paginator = Paginator(all_games, view.games_per_page)
        games_page = paginator.get_page(page)
        games_html = render(request, 'ui/games_list_items.html', {
            'games': games_page,
            'game_list_offset': (page - 1) * view.games_per_page,
            **card_ctx,
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
        **card_ctx,
        'show_sections_nav': True,
        **_project_urls_context(project.id),
    })


def project_hub(request, project_id):
    """
    Entry point for project-scoped UI, e.g. /glowbyte/
    """
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)
    base = _project_base(project.id)
    return render(request, 'ui/hub.html', {
        'project': project,
        'folders': [{'slug': 'games', 'title': 'Десяточки', 'description': 'Игры проекта', 'type': 'games'}],
        'section_games': [],
        'page_title': project.id,
        'show_sections_nav': False,
        'community_links': (
            [{'kind': 'telegram', 'title': 'Чат участников', 'href': 'https://t.me/joinchat/RUpU9KKhgLI4NDQy'}]
            if project.id == 'glowbyte'
            else []
        ),
        **_project_urls_context(project.id),
    })


def project_folder_games(request, project_id):
    """
    Games list inside a project scope, e.g. /glowbyte/games/ (same UI as /games/).
    """
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)
    base = _project_base(project.id)

    view = MainPageView()
    view.project_name = project.id
    view.games_per_page = 20
    all_games = view.get_games_list(request)
    card_ctx = _games_list_card_context(request)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        from django.core.paginator import Paginator
        page = int(request.GET.get('page', 1))
        paginator = Paginator(all_games, view.games_per_page)
        games_page = paginator.get_page(page)
        games_html = render(request, 'ui/games_list_items.html', {
            'games': games_page,
            'game_list_offset': (page - 1) * view.games_per_page,
            **card_ctx,
            **_project_urls_context(project.id),
        }).content.decode('utf-8')
        return JsonResponse({
            'games_html': games_html,
            'page': page,
            'has_next': games_page.has_next(),
            'total_pages': paginator.num_pages,
            'total_games': len(all_games),
        })

    return render(request, 'ui/folder_games.html', {
        'project': project,
        'games': all_games[:view.games_per_page],
        'total_games': len(all_games),
        'games_per_page': view.games_per_page,
        'page_title': 'Десяточки',
        **card_ctx,
        # In other projects we do not show main "sections" in the top nav.
        'section_games': [],
        'show_sections_nav': False,
        **_project_urls_context(project.id),
    })


def project_main_game_page(request, project_id, game_id):
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)
    base = _project_base(project.id)
    game = get_object_or_404(Game, id=game_id, project=project)

    play_mode, _ = _get_play_mode(request, game.project_id)
    if not request.user.is_authenticated and not personal_play_mode_locked(game):
        play_mode = 'personal'
    play_mode = effective_play_mode(play_mode, game)
    anon_key = _anon_key_from_request(request)
    team = None
    user = request.user if request.user.is_authenticated else None
    has_profile_user = has_profile(request.user)
    if has_profile_user:
        team = request.user.profile.team_on

    if not game.has_access('see_game_preview', team=team):
        raise Http404()
    if not game_has_started(game):
        raise Http404()

    mode = game.get_current_mode(Attempt(time=timezone.now()))

    actor_label = 'Вы'
    actor_value = 'гость'
    if play_mode == 'team':
        actor_value = ('команда {}'.format(team.visible_name)) if team else 'команда'
    else:
        if has_profile(request.user):
            fn = (request.user.profile.first_name or '').strip()
            ln = (request.user.profile.last_name or '').strip()
            name = ('{} {}'.format(fn, ln)).strip()
            actor_value = name or request.user.get_username()
        elif request.user.is_authenticated:
            actor_value = request.user.get_username()

    task_groups = (
        GameTaskGroup.objects.filter(game=game)
        .select_related('task_group')
        .annotate(n_tasks=Count('task_group__tasks', filter=Q(task_group__tasks__is_removed=False)))
        .order_by('number')
    )

    canonical_groups = [p.task_group for p in task_groups]
    solved_task_ids, tg_to_task_ids = _compute_solved_task_ids(
        game=game,
        task_groups=canonical_groups,
        team=team if play_mode == 'team' else None,
        user=user if play_mode != 'team' else None,
        anon_key=anon_key if play_mode != 'team' else None,
        mode=mode,
    )

    task_group_rows = []
    for p in task_groups:
        tg = p.task_group
        n_solved = len([tid for tid in tg_to_task_ids.get(tg.id, []) if tid in solved_task_ids])
        row_class = ''
        if p.n_tasks and n_solved >= p.n_tasks:
            row_class = 'new-task--solved'
        elif n_solved:
            row_class = 'new-task--partial'
        task_group_rows.append({
            'task_group': tg,
            'n_tasks': p.n_tasks,
            'n_solved': n_solved,
            'play_url': '{}/games/{}/{}/'.format(base, game.id, p.number),
            'is_fully_solved': bool(p.n_tasks) and n_solved >= p.n_tasks,
            'row_class': row_class,
            'title': '{} · {}'.format(p.number, p.name),
            'progress_text': '{} из {} {} решено'.format(n_solved, p.n_tasks, _ru_iz_punkt_word(p.n_tasks)),
        })

    return render(request, 'ui/game_page.html', {
        'project': project,
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
        'back_url': (base + '/games/') if base else '/games/',
        'lock_personal_play_mode': personal_play_mode_locked(game),
        'section_games': [],
        'show_sections_nav': False,
        **_project_urls_context(project.id),
    })


def project_results_page(request, project_id, game_id):
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)
    base = _project_base(project.id)
    game = get_object_or_404(Game, id=game_id, project=project)
    team = request.user.profile.team_on if has_profile(request.user) else None
    if not game.has_access('see_results', mode='general', team=team):
        raise Http404()
    snap = GameResultsSnapshot.objects.filter(game=game, mode='general').first()
    if snap and snap.payload:
        data = snapshot_to_results_context(game, snap.payload)
    else:
        data = _new_results_compute(game, mode='general')
    data = _paginate_results_rows(request, data, per_page=50)
    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game)
    me_personal = None
    me_anon_participant = None
    if play_mode == 'personal':
        if request.user.is_authenticated:
            me_personal = PersonalResultsParticipant(user=request.user)
        else:
            ak = _anon_key_from_request(request)
            if ak:
                me_anon_participant = PersonalResultsParticipant(anon_key=ak)
    return render(request, 'ui/results.html', {
        'project': project,
        'mode': 'general',
        'game': game,
        'team': team,
        'me_personal': me_personal,
        'me_anon_participant': me_anon_participant,
        'back_url': '{}/games/{}/'.format(base, game.id),
        **data,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'page_title': 'Результаты: {}'.format(game.get_no_html_name() if hasattr(game, 'get_no_html_name') else game.name),
        'lock_personal_play_mode': personal_play_mode_locked(game),
        'section_games': [],
        'show_sections_nav': False,
        **_project_urls_context(project.id),
    })


def project_tournament_results_page(request, project_id, game_id):
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)
    base = _project_base(project.id)
    game = get_object_or_404(Game, id=game_id, project=project)
    team = request.user.profile.team_on if has_profile(request.user) else None
    if not game.has_access('see_tournament_results', team=team):
        raise Http404()
    snap = GameResultsSnapshot.objects.filter(game=game, mode='tournament').first()
    if snap and snap.payload:
        data = snapshot_to_results_context(game, snap.payload)
    else:
        data = _new_results_compute(game, mode='tournament')
    data = _paginate_results_rows(request, data, per_page=50)
    return render(request, 'ui/results.html', {
        'project': project,
        'mode': 'tournament',
        'game': game,
        'team': team,
        'me_personal': None,
        'me_anon_participant': None,
        'back_url': '{}/games/{}/'.format(base, game.id),
        **data,
        'play_mode': effective_play_mode(_get_play_mode(request, game.project_id)[0], game),
        'play_mode_project_id': game.project_id,
        'page_title': 'Результаты турнира: {}'.format(game.get_no_html_name() if hasattr(game, 'get_no_html_name') else game.name),
        'lock_personal_play_mode': personal_play_mode_locked(game),
        'section_games': [],
        'show_sections_nav': False,
        **_project_urls_context(project.id),
    })


def project_task_group_page(request, project_id, game_id, task_group_number):
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)
    base = _project_base(project.id)
    game = get_object_or_404(Game, id=game_id, project=project)

    play_mode, play_mode_key = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game)
    anon_key = None

    if not request.user.is_authenticated:
        if personal_play_mode_locked(game):
            from urllib.parse import quote
            return redirect('/accounts/login/?next={}'.format(quote(request.get_full_path())))
        play_mode = 'personal'
        anon_key = _anon_key_from_request(request)
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
    placement = (
        GameTaskGroup.objects.select_related('task_group', 'task_group__rules')
        .filter(game=game, number=task_group_number)
        .first()
    )
    if not placement:
        next_p = (
            GameTaskGroup.objects.filter(game=game, number__gt=task_group_number)
            .order_by('number')
            .first()
        )
        prev_p = (
            GameTaskGroup.objects.filter(game=game, number__lt=task_group_number)
            .order_by('-number')
            .first()
        )
        fallback = next_p or prev_p
        if fallback:
            return redirect('project_task_group', project_id=project.id, game_id=game.id, task_group_number=fallback.number)
        raise Http404()
    task_group = placement.task_group
    prev_tg = (
        GameTaskGroup.objects.filter(game=game, number__lt=placement.number).order_by('-number').first()
    )
    next_tg = (
        GameTaskGroup.objects.filter(game=game, number__gt=placement.number).order_by('number').first()
    )
    tasks = sorted(task_group.tasks.visible(), key=lambda t: t.key_sort())
    ctx_dicts = build_task_group_task_context_dicts(game, task_group, tasks, team, user, anon_key, mode)
    return render(request, 'ui/task_group.html', {
        'project': project,
        'game': game,
        'task_group': task_group,
        'tasks': tasks,
        'attempts_info_by_task_id': ctx_dicts['attempts_info_by_task_id'],
        'replacements_lines_data': ctx_dicts['replacements_lines_data'],
        'proportions_chips': ctx_dicts['proportions_chips'],
        'wall_max_points_meta_by_task_id': ctx_dicts['wall_max_points_meta_by_task_id'],
        'likes_meta_by_task_id': ctx_dicts['likes_meta_by_task_id'],
        'can_like': True,
        'has_profile_user': has_profile(request.user),
        'mode': mode,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'anon_key': anon_key,
        'team': team,
        'show_palindrome_rules': False,
        'section_rules_type': None,
        'section_tutorial_html': None,
        'prev_task_group_url': '{}/games/{}/{}/'.format(base, game.id, prev_tg.number) if prev_tg else None,
        'next_task_group_url': '{}/games/{}/{}/'.format(base, game.id, next_tg.number) if next_tg else None,
        'tg_number': placement.number,
        'tg_name': placement.name,
        'back_url': '{}/games/{}/'.format(base, game.id),
        'page_title': '{} · {}'.format(game.outside_name or game.name, placement.name),
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
        'lock_personal_play_mode': personal_play_mode_locked(game),
        'section_games': [],
        'show_sections_nav': False,
        **_project_urls_context(project.id),
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
    # Шаблон game_page использует team в фильтрах access_see_results и т.д.;
    # должен совпадать с командой для see_game_preview (ниже team перезаписывается под play_mode).
    team_for_access = team
    task_groups = (
        GameTaskGroup.objects.filter(game=game)
        .select_related('task_group', 'task_group__rules')
        .annotate(n_tasks=Count('task_group__tasks', filter=Q(task_group__tasks__is_removed=False)))
        .order_by('number')
    )
    play_mode, play_mode_key = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game)

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
            anon_key = _anon_key_from_request(request)

    canonical_groups = [p.task_group for p in task_groups]
    solved_task_ids, tg_to_task_ids = _compute_solved_task_ids(
        game=game,
        task_groups=canonical_groups,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
    )

    task_group_rows = []
    for p in task_groups:
        tg = p.task_group
        tg_task_ids = tg_to_task_ids.get(tg.id, [])
        n_solved = len([tid for tid in tg_task_ids if tid in solved_task_ids])
        is_fully_solved = bool(tg_task_ids) and n_solved >= len(tg_task_ids)
        row_class = ''
        if p.n_tasks and n_solved >= p.n_tasks:
            row_class = 'new-task--solved'
        elif n_solved:
            row_class = 'new-task--partial'

        is_fully_solved = bool(tg_task_ids) and all(
            tid in solved_task_ids for tid in tg_to_task_ids.get(tg.id, [])
        )
        task_group_rows.append({
            'task_group': tg,
            'game': game,
            'n_tasks': p.n_tasks,
            'n_solved': n_solved,
            'play_url': '/games/{}/{}/'.format(game_id, p.number),
            'is_fully_solved': is_fully_solved,
            'row_class': row_class,
            'title': '{} · {}'.format(p.number, p.name),
            'progress_text': '{} из {} {} решено'.format(n_solved, p.n_tasks, _ru_iz_punkt_word(p.n_tasks)),
        })
    section_task_groups_rules_html = _section_task_groups_rules_modal_html(task_groups)
    if section_task_groups_rules_html:
        section_rules_type = None
        section_tutorial_html = None
        show_palindrome_rules = False
    else:
        section_rules_type = game_id if game_id in SECTION_RULES_GAME_IDS else None
        section_tutorial_html = None
        if section_rules_type:
            try:
                page = HTMLPage.objects.get(name='section_tutorial_' + section_rules_type)
                section_tutorial_html = page.html or ''
            except HTMLPage.DoesNotExist:
                pass
        show_palindrome_rules = game_id == PALINDROMES_GAME_ID
    return render(request, 'ui/game_page.html', {
        'game': game,
        'task_group_rows': task_group_rows,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'page_title': game.outside_name or game.name,
        'show_palindrome_rules': show_palindrome_rules,
        'section_rules_type': section_rules_type,
        'section_tutorial_html': section_tutorial_html,
        'section_task_groups_rules_html': section_task_groups_rules_html,
        'is_main_game': False,
        'task_groups_heading': 'Наборы заданий',
        'task_groups_empty_text': 'В этом разделе пока нет групп заданий. Добавьте их в админке.',
        'back_url': '/',
        'lock_personal_play_mode': personal_play_mode_locked(game),
        'team': team_for_access,
        'show_sections_nav': True,
        **_project_urls_context(NEW_UI_PROJECT),
    })


def new_main_game_page(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    if game.project_id != NEW_UI_PROJECT:
        raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    if not request.user.is_authenticated and not personal_play_mode_locked(game):
        play_mode = 'personal'
    play_mode = effective_play_mode(play_mode, game)
    anon_key = _anon_key_from_request(request)
    team = None
    user = request.user if request.user.is_authenticated else None
    has_profile_user = has_profile(request.user)
    if has_profile_user:
        team = request.user.profile.team_on

    if not game.has_access('see_game_preview', team=team):
        raise Http404()

    # Как в new_task_group_page: обычные игры до start_time — 404 (прямой URL не даёт «превью» списка заданий).
    # Разделы (sections): страница игры доступна без ограничения по времени старта.
    if game.project_id != NEW_UI_SECTIONS_PROJECT:
        if not game_has_started(game):
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
        GameTaskGroup.objects.filter(game=game)
        .select_related('task_group')
        .annotate(n_tasks=Count('task_group__tasks', filter=Q(task_group__tasks__is_removed=False)))
        .order_by('number')
    )

    canonical_groups = [p.task_group for p in task_groups]
    solved_task_ids, tg_to_task_ids = _compute_solved_task_ids(
        game=game,
        task_groups=canonical_groups,
        team=team if play_mode == 'team' else None,
        user=user if play_mode != 'team' else None,
        anon_key=anon_key if play_mode != 'team' else None,
        mode=mode,
    )

    task_group_rows = []
    for p in task_groups:
        tg = p.task_group
        n_solved = len([tid for tid in tg_to_task_ids.get(tg.id, []) if tid in solved_task_ids])
        row_class = ''
        if p.n_tasks and n_solved >= p.n_tasks:
            row_class = 'new-task--solved'
        elif n_solved:
            row_class = 'new-task--partial'
        task_group_rows.append({
            'task_group': tg,
            'n_tasks': p.n_tasks,
            'n_solved': n_solved,
            'play_url': '/games/{}/{}/'.format(game.id, p.number),
            'is_fully_solved': bool(p.n_tasks) and n_solved >= p.n_tasks,
            'row_class': row_class,
            'title': '{} · {}'.format(p.number, p.name),
            'progress_text': '{} из {} {} решено'.format(n_solved, p.n_tasks, _ru_iz_punkt_word(p.n_tasks)),
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
        'lock_personal_play_mode': personal_play_mode_locked(game),
        'show_sections_nav': True,
        **_project_urls_context(game.project_id),
    })


def _new_results_compute(game, mode):
    team_to_list_attempts_info = {}
    team_to_score = {}
    team_to_max_best_time = {}
    team_task_to_attempts_info = {}

    # 2 queries: placements + tasks (prefetched on canonical task_group)
    placements = sorted(
        game.task_group_links.select_related('task_group').prefetch_related(
            Prefetch(
                'task_group__tasks',
                queryset=Task.objects.visible().filter(~Q(task_type='text_with_forms')),
                to_attr='result_tasks',
            )
        ),
        key=lambda p: p.number,
    )
    task_group_to_tasks = {}
    for p in placements:
        tg = p.task_group
        task_group_to_tasks[p.number] = sorted(
            getattr(tg, 'result_tasks', []) or [], key=lambda t: t.key_sort()
        )

    tasks_flat = [t for p in placements for t in task_group_to_tasks[p.number]]
    task_ids = [t.id for t in tasks_flat]

    # 2 queries: all attempts + all hint attempts for the whole game at once.
    # Sections (general): same TaskGroup can appear in several games; attempts are stored
    # with Attempt.game set to the game where the player submitted. HintAttempt has no
    # game FK, so filtering attempts by game=current hid cross-game team attempts while
    # hints still showed — unify by not scoping attempts to one game here.
    bulk_game = game
    if mode == 'general' and getattr(game, 'project_id', None) == NEW_UI_SECTIONS_PROJECT:
        bulk_game = None
    bulk_rows = Attempt.manager.get_bulk_game_actor_rows(task_ids, mode=mode, game=bulk_game)

    for task in tasks_flat:
        for participant, attempts_info in bulk_rows.get(task.id, []):
            if mode == 'tournament' and not isinstance(participant, Team):
                continue
            if not (attempts_info.attempts or attempts_info.hint_attempts):
                continue

            if participant not in team_to_score:
                team_to_score[participant] = 0

            task_points = 0
            if attempts_info.best_attempt is not None:
                task_points = attempts_info.best_attempt.points
            if task_points and task_points > 0:
                team_to_score[participant] += max(0, task_points - attempts_info.get_sum_hint_penalty())
                if participant not in team_to_max_best_time:
                    team_to_max_best_time[participant] = attempts_info.best_attempt.time
                else:
                    team_to_max_best_time[participant] = max(team_to_max_best_time[participant], attempts_info.best_attempt.time)

            team_task_to_attempts_info[(participant, task)] = attempts_info

    for team in team_to_score.keys():
        for p in placements:
            for task in task_group_to_tasks[p.number]:
                team_to_list_attempts_info.setdefault(team, [])
                team_to_list_attempts_info[team].append(team_task_to_attempts_info.get((team, task)))

    teams_sorted = []
    for participant, score in team_to_score.items():
        # `Attempt.time` is typically timezone-aware; using naive `datetime.now()`
        # as a fallback can make sorting crash with "can't compare offset-naive and offset-aware datetimes".
        max_best_time = team_to_max_best_time.get(participant) or timezone.now()
        # Sort by a comparable primitive to avoid tz-awareness issues.
        max_best_time_ts = max_best_time.timestamp() if hasattr(max_best_time, "timestamp") else float("inf")
        teams_sorted.append((-score, max_best_time_ts, participant))
    teams_sorted = [p for anti_score, max_best_time_ts, p in sorted(teams_sorted, key=lambda t: (t[0], t[1], str(t[2])))]

    team_to_place = {}
    for i, participant in enumerate(teams_sorted):
        team_to_place[participant] = 1 + i
        if i:
            prev = teams_sorted[i - 1]
            if team_to_score[participant] == team_to_score[prev]:
                team_to_place[participant] = team_to_place[prev]

    # Prepare per-cell metadata for templates: color by points vs max.
    tasks_flat = []
    for p in placements:
        for task in task_group_to_tasks[p.number]:
            tasks_flat.append(task)

    def _to_float(x):
        try:
            return float(x)
        except Exception:
            return 0.0

    # Precompute once per task — get_results_max_points() can be expensive
    # (e.g. replacements_lines tasks run a regex parse on every call).
    task_max_points = {}
    for task in tasks_flat:
        try:
            mp = (task.get_results_max_points() if hasattr(task, 'get_results_max_points')
                  else getattr(task, 'get_points', None)() if hasattr(task, 'get_points')
                  else getattr(task, 'points', 0))
            task_max_points[task.id] = _to_float(mp)
        except Exception:
            task_max_points[task.id] = 0.0

    team_to_cells = {}
    for participant in teams_sorted:
        cells = []
        attempts_list = team_to_list_attempts_info.get(participant, [])
        for idx, task in enumerate(tasks_flat):
            ai = attempts_list[idx] if idx < len(attempts_list) else None
            max_points = task_max_points[task.id]
            points = 0.0
            has_attempts = False
            n_attempts = 0
            hint_numbers = []
            if ai:
                try:
                    n_attempts = int(
                        ai.get_n_attempts()
                        if callable(getattr(ai, 'get_n_attempts', None))
                        else (ai.get_n_attempts or 0)
                    )
                    has_attempts = n_attempts > 0
                except Exception:
                    has_attempts = False
                    n_attempts = 0
                try:
                    points = _to_float(ai.get_result_points())
                except Exception:
                    points = 0.0
                try:
                    hint_numbers = sorted([
                        ha.hint.number
                        for ha in (getattr(ai, 'hint_attempts', None) or [])
                        if getattr(ha, 'is_real_request', False)
                    ])
                except Exception:
                    hint_numbers = []

            cls = ''
            if has_attempts:
                if max_points > 0 and points >= max_points - 1e-9:
                    cls = 'cell-full'
                elif points <= 0:
                    cls = 'cell-zero'
                else:
                    cls = 'cell-partial'

            cells.append({
                'cls': cls,
                'n_attempts': n_attempts,
                'result_points': points,
                'hint_numbers': hint_numbers,
            })
        team_to_cells[participant] = cells

    task_group_headers = [
        _ResultsTaskGroupHeader(p.number, p.name, len(task_group_to_tasks[p.number]))
        for p in placements
    ]

    return {
        'task_groups': task_group_headers,
        'task_group_to_tasks': task_group_to_tasks,
        'teams_sorted': teams_sorted,
        'team_to_list_attempts_info': team_to_list_attempts_info,
        'team_to_cells': team_to_cells,
        'team_to_score': team_to_score,
        'team_to_place': team_to_place,
        'team_to_max_best_time': team_to_max_best_time,
    }


def _paginate_results_rows(request, data, per_page=50):
    """
    Paginate the results rows (teams_sorted) without touching score/place dicts.
    Places remain global (computed for full list), only the rendered rows are sliced.
    """
    rows = list(data.get('teams_sorted') or [])
    paginator = Paginator(rows, per_page)
    page_obj = paginator.get_page(request.GET.get('page') or 1)

    # Keep templates working by slicing teams_sorted to the visible page.
    out = dict(data)
    out['teams_sorted'] = list(page_obj.object_list)
    out['page_obj'] = page_obj
    out['paginator'] = paginator
    out['is_paginated'] = paginator.num_pages > 1

    qs = request.GET.copy()
    try:
        qs.pop('page', None)
    except Exception:
        pass
    rest = qs.urlencode()
    out['page_qs_prefix'] = ('?' + rest + '&') if rest else '?'
    out['page_size'] = per_page
    out['page_total_rows'] = paginator.count
    return out


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
    data = _paginate_results_rows(request, data, per_page=50)
    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game)
    me_personal = None
    me_anon_participant = None
    if play_mode == 'personal':
        if request.user.is_authenticated:
            me_personal = PersonalResultsParticipant(user=request.user)
        else:
            ak = _anon_key_from_request(request)
            if ak:
                me_anon_participant = PersonalResultsParticipant(anon_key=ak)
    return render(request, 'ui/results.html', {
        'mode': 'general',
        'game': game,
        'team': team,
        'me_personal': me_personal,
        'me_anon_participant': me_anon_participant,
        'back_url': '/games/{}/'.format(game.id),
        **data,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'page_title': 'Результаты: {}'.format(game.get_no_html_name() if hasattr(game, 'get_no_html_name') else game.name),
        'lock_personal_play_mode': personal_play_mode_locked(game),
        'show_sections_nav': True,
        **_project_urls_context(game.project_id),
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
        'me_personal': None,
        'me_anon_participant': None,
        'back_url': '/games/{}/'.format(game.id),
        **data,
        'play_mode': effective_play_mode(_get_play_mode(request, game.project_id)[0], game),
        'play_mode_project_id': game.project_id,
        'page_title': 'Результаты турнира: {}'.format(game.get_no_html_name() if hasattr(game, 'get_no_html_name') else game.name),
        'lock_personal_play_mode': personal_play_mode_locked(game),
        'show_sections_nav': True,
        **_project_urls_context(game.project_id),
    })


def new_section_results_page(request, game_id):
    """Результаты игры из project «sections»: одна таблица, без турнира/«общего»."""
    project = Project.objects.filter(id=NEW_UI_SECTIONS_PROJECT).first()
    if not project:
        raise Http404()
    game = Game.objects.filter(project=project, id=game_id).first()
    if not game:
        raise Http404()
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    if not game.has_access('see_results', mode='general', team=team):
        raise Http404()

    snap = GameResultsSnapshot.objects.filter(game=game, mode='general').first()
    if snap and snap.payload:
        data = snapshot_to_results_context(game, snap.payload)
    else:
        data = _new_results_compute(game, mode='general')
    # Section results can have many participants. Support progressive loading
    # (10 rows per page) so the HTML page can build incrementally.
    progressive_page_size = 10
    data = _paginate_results_rows(request, data, per_page=progressive_page_size)
    if request.GET.get('partial') == '1':
        return render(request, 'new/partials/results_rows.html', {
            'mode': 'general',
            'section_results': True,
            'game': game,
            'team': team,
            'me_personal': None,
            'me_anon_participant': None,
            **data,
        })
    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game)
    me_personal = None
    me_anon_participant = None
    if play_mode == 'personal':
        if request.user.is_authenticated:
            me_personal = PersonalResultsParticipant(user=request.user)
        else:
            ak = _anon_key_from_request(request)
            if ak:
                me_anon_participant = PersonalResultsParticipant(anon_key=ak)
    return render(request, 'ui/results.html', {
        'mode': 'general',
        'section_results': True,
        'game': game,
        'team': team,
        'me_personal': me_personal,
        'me_anon_participant': me_anon_participant,
        'back_url': '/section/{}/'.format(game.id),
        'progressive_results': True,
        'progressive_page_size': progressive_page_size,
        **data,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'page_title': 'Результаты: {}'.format(game.get_no_html_name() if hasattr(game, 'get_no_html_name') else game.name),
        'lock_personal_play_mode': personal_play_mode_locked(game),
        'show_sections_nav': True,
        **_project_urls_context(NEW_UI_PROJECT),
    })


def build_task_group_task_context_dicts(game, task_group, tasks, team, user, anon_key, mode):
    """
    Shared context for task_group.html and new/partials/task_card.html
    (attempts, walls, replacements_lines, likes, proportions pool).
    """
    attempts_info_by_task_id = {
        t.id: Attempt.manager.get_attempts_info(
            team=team, task=t, mode=mode, user=user, anon_key=anon_key, game=game,
        )
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
    replacements_lines_data = {}
    for t in tasks:
        if t.task_type == 'replacements_lines':
            parsed = parse_replacements_lines_text(t.text, (t.checker_data or '').strip() or None)
            n_lines = len(parsed['left_lines'])
            line_solved = [False] * n_lines
            line_attempts = [0] * n_lines
            answers_by_line = parsed.get('answers', [])
            accept_by_line = parsed.get('answer_accept') or []
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
                            opts_row = (
                                accept_by_line[idx]
                                if idx < len(accept_by_line)
                                else [[c] for c in correct_answers]
                            )
                            for j in range(min(len(user_answers), len(correct_answers))):
                                opts = opts_row[j] if j < len(opts_row) else [correct_answers[j]]
                                if any(clean_text(user_answers[j]) == clean_text(o) for o in opts):
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
            slot_counts = [len(answers_by_line[i]) for i in range(n_lines)]
            replacements_lines_data[t.id] = {
                'parsed': parsed,
                'line_solved': line_solved,
                'line_done': line_done,
                'line_attempts': line_attempts,
                'slot_correct': slot_correct,
                'n_lines': n_lines,
                'slot_counts': slot_counts,
                'max_attempts': t.get_max_attempts(),
                'max_points_total': t.get_results_max_points(),
            }
    proportions_chips = []
    if task_group.view == 'proportions':
        proportions_chips = build_proportions_chips_for_tasks(tasks)
        for c in proportions_chips:
            tid = c.get('task_id')
            ai = attempts_info_by_task_id.get(tid) if tid is not None else None
            c['hide_from_pool'] = bool(ai and ai.is_solved())
    return {
        'attempts_info_by_task_id': attempts_info_by_task_id,
        'wall_max_points_meta_by_task_id': wall_max_points_meta_by_task_id,
        'likes_meta_by_task_id': likes_meta_by_task_id,
        'replacements_lines_data': replacements_lines_data,
        'proportions_chips': proportions_chips,
    }


def new_task_group_page(request, game_id, task_group_number):
    game = get_object_or_404(Game, id=game_id)
    play_mode, play_mode_key = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game)
    anon_key = None

    if not request.user.is_authenticated:
        if personal_play_mode_locked(game):
            from urllib.parse import quote
            return redirect('/accounts/login/?next={}'.format(quote(request.get_full_path())))
        # До логина разрешаем только личный режим (не в турнире).
        play_mode = 'personal'
        anon_key = _anon_key_from_request(request)
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
    placement = (
        GameTaskGroup.objects.select_related('task_group', 'task_group__rules')
        .filter(game=game, number=task_group_number)
        .first()
    )
    if not placement:
        next_p = (
            GameTaskGroup.objects.filter(game=game, number__gt=task_group_number)
            .order_by('number')
            .first()
        )
        prev_p = (
            GameTaskGroup.objects.filter(game=game, number__lt=task_group_number)
            .order_by('-number')
            .first()
        )
        fallback = next_p or prev_p
        if fallback:
            return redirect('new_task_group', game_id=game.id, task_group_number=fallback.number)
        raise Http404()
    task_group = placement.task_group
    prev_tg = (
        GameTaskGroup.objects.filter(game=game, number__lt=placement.number).order_by('-number').first()
    )
    next_tg = (
        GameTaskGroup.objects.filter(game=game, number__gt=placement.number).order_by('number').first()
    )
    tasks = sorted(task_group.tasks.visible(), key=lambda t: t.key_sort())
    section_rules_type = game.id if game.id in SECTION_RULES_GAME_IDS else None
    section_tutorial_html = None
    if section_rules_type:
        try:
            page = HTMLPage.objects.get(name='section_tutorial_' + section_rules_type)
            section_tutorial_html = page.html or ''
        except HTMLPage.DoesNotExist:
            pass
    show_palindrome_rules = game.id == PALINDROMES_GAME_ID
    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        tg_rules = placement.task_group.rules
        if tg_rules and (tg_rules.html or '').strip():
            section_rules_type = None
            section_tutorial_html = None
            show_palindrome_rules = False
    ctx_dicts = build_task_group_task_context_dicts(game, task_group, tasks, team, user, anon_key, mode)
    return render(request, 'ui/task_group.html', {
        'game': game,
        'task_group': task_group,
        'tasks': tasks,
        'attempts_info_by_task_id': ctx_dicts['attempts_info_by_task_id'],
        'replacements_lines_data': ctx_dicts['replacements_lines_data'],
        'proportions_chips': ctx_dicts['proportions_chips'],
        'wall_max_points_meta_by_task_id': ctx_dicts['wall_max_points_meta_by_task_id'],
        'likes_meta_by_task_id': ctx_dicts['likes_meta_by_task_id'],
        'can_like': True,
        'has_profile_user': has_profile(request.user),
        'mode': mode,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'anon_key': anon_key,
        'team': team,
        'show_palindrome_rules': show_palindrome_rules,
        'section_rules_type': section_rules_type,
        'section_tutorial_html': section_tutorial_html,
        'prev_task_group_url': '/games/{}/{}/'.format(game.id, prev_tg.number) if prev_tg else None,
        'next_task_group_url': '/games/{}/{}/'.format(game.id, next_tg.number) if next_tg else None,
        'tg_number': placement.number,
        'tg_name': placement.name,
        'back_url': (
            '/section/{}/'.format(game.id)
            if game.project_id == NEW_UI_SECTIONS_PROJECT
            else (
                '/games/{}/'.format(game.id)
                if game.project_id == NEW_UI_PROJECT
                else '/'
            )
        ),
        'page_title': '{} · {}'.format(game.outside_name or game.name, placement.name),
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
        'lock_personal_play_mode': personal_play_mode_locked(game),
        'show_sections_nav': True,
        **_project_urls_context(game.project_id),
    })


def _replacements_lines_line_done_list(task, attempts_info):
    """
    Какие строки задания «Замены» считаются сданными для актора (как rld.line_done в new_task_group_page).
    """
    if task.task_type != 'replacements_lines':
        return []
    parsed = parse_replacements_lines_text(task.text, (task.checker_data or '').strip() or None)
    n_lines = len(parsed['left_lines'])
    if not n_lines:
        return []
    answers_by_line = parsed.get('answers', [])
    accept_by_line = parsed.get('answer_accept') or []
    slot_correct = [
        [False] * len(answers_by_line[i]) for i in range(n_lines)
    ]
    line_done = [False] * n_lines
    solved_lines_from_state = set()
    attempts = attempts_info.attempts if attempts_info else []
    for a in attempts:
        try:
            p = json.loads(a.text)
            idx = int(p.get('line_index', -1))
            if 0 <= idx < n_lines:
                user_answers = p.get('answers', []) or []
                correct_answers = answers_by_line[idx] if idx < len(answers_by_line) else []
                opts_row = (
                    accept_by_line[idx]
                    if idx < len(accept_by_line)
                    else [[c] for c in correct_answers]
                )
                for j in range(min(len(user_answers), len(correct_answers))):
                    opts = opts_row[j] if j < len(opts_row) else [correct_answers[j]]
                    if any(clean_text(user_answers[j]) == clean_text(o) for o in opts):
                        slot_correct[idx][j] = True
        except (ValueError, TypeError):
            pass
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
        if not line_done[i]:
            line_done[i] = bool(slot_correct[i]) and all(slot_correct[i])
    return line_done


def _answer_popup_html(answer_text, answer_comment=None):
    """HTML for the new-task answer modal: bold answer plus optional comment (HTML allowed in comment, like legacy answer.html)."""
    c = (answer_comment or '').strip()
    if c:
        return format_html(
            '<div style="font-weight:700">{}</div>'
            '<div class="new-login-hint new-answer-comment" style="margin-top:0.75rem">{}</div>',
            answer_text or '',
            mark_safe(c),
        )
    return format_html('<div style="font-weight:700">{}</div>', answer_text or '')


@require_http_methods(['GET'])
def new_get_answer(request, task_id):
    task = get_public_task_or_404(task_id)
    game = game_from_request_for_task(request, task)
    if game is None:
        raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game)
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
            anon_key = _anon_key_from_request(request)
            if not anon_key:
                raise Http404()
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise Http404()

    mode = game.get_current_mode(Attempt(time=timezone.now()))
    attempts_info = Attempt.manager.get_attempts_info(team=team, user=user, anon_key=anon_key, task=task, mode=mode)
    if mode != 'general' and not attempts_info.is_solved():
        return JsonResponse({'html': '<div class="new-login-hint">Ответ доступен после верного решения.</div>'})

    if task.task_type == 'replacements_lines':
        return JsonResponse({
            'html': (
                '<div class="new-login-hint">Для замен ответ показывается отдельно по каждой строке '
                '(кнопка «Ответ» у строки после её решения).</div>'
            ),
        })

    return JsonResponse({'html': _answer_popup_html(task.answer, task.answer_comment)})


@require_http_methods(['GET'])
def new_get_replacements_line_answer(request, task_id, line_index):
    task = get_public_task_or_404(task_id)
    if task.task_type != 'replacements_lines':
        raise Http404()
    game = game_from_request_for_task(request, task)
    if game is None:
        raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game)
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
            anon_key = _anon_key_from_request(request)
            if not anon_key:
                raise Http404()
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise Http404()

    mode = game.get_current_mode(Attempt(time=timezone.now()))
    attempts_info = Attempt.manager.get_attempts_info(team=team, user=user, anon_key=anon_key, task=task, mode=mode)
    try:
        line_index_int = int(line_index)
    except (TypeError, ValueError):
        line_index_int = -1
    line_done_list = _replacements_lines_line_done_list(task, attempts_info)
    if mode != 'general':
        if not attempts_info.is_solved():
            if line_index_int < 0 or line_index_int >= len(line_done_list) or not line_done_list[line_index_int]:
                return JsonResponse({'html': '<div class="new-login-hint">Ответ доступен после верного решения.</div>'})

    # Для replacements_lines ответы живут в checker_data (output-текст).
    lines = (task.checker_data or '').splitlines()
    if line_index_int < 0 or line_index_int >= len(lines):
        raw = ''
    else:
        raw = lines[line_index_int]
    text = canonical_replacements_checker_line(raw)
    if not text.strip():
        return JsonResponse({'html': '<div class="new-login-hint">Нет ответа.</div>'})
    return JsonResponse({'html': _answer_popup_html(text, task.answer_comment)})


@require_http_methods(['POST'])
def new_like_dislike(request, task_id):
    task = get_public_task_or_404(task_id)
    game = game_from_request_for_task(request, task)
    if game is None:
        raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game)
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


def _game_from_next_path(path):
    """Если next ведёт на страницу игры — вернуть Game или None."""
    if not path:
        return None
    from urllib.parse import urlparse
    from django.urls import Resolver404, resolve
    try:
        match = resolve(urlparse(path).path)
    except Resolver404:
        return None
    game_id = match.kwargs.get('game_id')
    if not game_id:
        return None
    return Game.objects.filter(pk=game_id).first()


@require_http_methods(['GET'])
def new_set_play_mode(request):
    mode = request.GET.get('mode')
    next_url = request.GET.get('next') or '/'
    project_id = request.GET.get('project') or NEW_UI_PROJECT
    if mode == 'personal':
        g = _game_from_next_path(next_url)
        if g is not None and personal_play_mode_locked(g):
            mode = 'team'
    if mode in ('team', 'personal'):
        request.session[_session_play_mode_key(project_id)] = mode
    return redirect(next_url)


# Модалка «перенести анонимные решения» показывается только при достаточном числе посылок,
# иначе ключ interoves_anon_key есть у любого гостя до входа.
MIN_ANON_MIGRATE_PROMPT_ATTEMPTS = 10


@login_required
@require_http_methods(['GET'])
def new_anon_migrate_count(request):
    if not has_profile(request.user):
        raise Http404()
    anon_key = request.GET.get('anon_key')
    if not anon_key:
        return JsonResponse({'attempts': 0, 'show_prompt': False})
    n = Attempt.objects.filter(
        anon_key=anon_key, user__isnull=True, team__isnull=True
    ).count()
    return JsonResponse({
        'attempts': n,
        'show_prompt': n >= MIN_ANON_MIGRATE_PROMPT_ATTEMPTS,
    })


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
def new_profile(request, project_id=None):
    if not has_profile(request.user):
        messages.error(request, 'Профиль недоступен.')
        scoped = _scoped_project_id(request)
        if scoped:
            return redirect('project_hub', project_id=scoped)
        return redirect('new_hub')
    profile = request.user.profile
    connected = set(SocialAccount.objects.filter(user=request.user).values_list('provider', flat=True))
    if request.method == 'POST':
        form = ProfileSettingsForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Профиль сохранён.')
            return _profile_redirect(request)
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

    scoped = _scoped_project_id(request)
    ctx = {
        'form': form,
        'connected_providers': connected,
        'tz_options': tz_options,
        'page_title': 'Профиль',
    }
    _merge_nav_project_for_scope(ctx, request, scoped)
    return render(request, 'ui/profile.html', ctx)


def _post_make_new_team_primary(request):
    """POST make_primary: 1 / true — новая команда активна; 0 / false — оставить текущую."""
    v = (request.POST.get('make_primary') or '1').strip().lower()
    return v not in ('0', 'false', 'no', 'off')


def _member_teams_active_first(profile):
    """Все команды членства; активная первая (для переключателя на странице команды)."""
    rows = list(
        Team.objects.filter(member_links__profile=profile).distinct().order_by('visible_name', 'name')
    )
    tid = profile.team_on_id
    if not tid:
        return rows, []
    primary = next((t for t in rows if t.pk == tid), None)
    if not primary:
        return rows, []
    others = [t for t in rows if t.pk != tid]
    return [primary] + others, others


def _new_team_ui_context(request):
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    scoped = _scoped_project_id(request)
    if scoped:
        get_object_or_404(Project, id=scoped)
        base = _project_base(scoped)
        back = request.build_absolute_uri(base + '/team/')
        url_map = _project_team_page_urls(scoped)
    else:
        back = request.build_absolute_uri('/team/')
        url_map = _main_team_page_urls()
    teams = sorted(Team.objects.filter(project=project, is_hidden=False), key=lambda t: t.visible_name)
    profile = request.user.profile
    profile.repair_primary_team()
    member_teams, member_teams_others = _member_teams_active_first(profile)
    secondary_teams = list(profile.other_member_teams()) if profile.team_on_id else []
    ctx = {
        'project': project,
        'teams': teams,
        'new_team_url': back,
        'member_teams': member_teams,
        'member_teams_others': member_teams_others,
        'secondary_teams': secondary_teams,
        'team_primary_modal': len(member_teams) > 0,
        **url_map,
    }
    return _merge_nav_project_for_scope(ctx, request, scoped)


@login_required
@require_http_methods(['GET'])
def new_team(request, project_id=None):
    if not has_profile(request.user):
        messages.error(request, 'Сначала войдите и создайте профиль.')
        scoped = _scoped_project_id(request)
        if scoped:
            return redirect('project_hub', project_id=scoped)
        return redirect('new_hub')
    ctx = _new_team_ui_context(request)
    ctx['team_section'] = 'hub'
    ctx['page_title'] = 'Команда'
    return render(request, 'ui/team.html', ctx)


@login_required
@require_http_methods(['GET'])
def new_team_join_page(request, project_id=None):
    if not has_profile(request.user):
        messages.error(request, 'Сначала войдите и создайте профиль.')
        scoped = _scoped_project_id(request)
        if scoped:
            return redirect('project_hub', project_id=scoped)
        return redirect('new_hub')
    ctx = _new_team_ui_context(request)
    ctx['team_section'] = 'join'
    ctx['page_title'] = 'Вступить в команду'
    return render(request, 'ui/team.html', ctx)


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
def new_team_name_check(request, project_id=None):
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    name = (request.GET.get('name') or '').strip()
    if not name:
        return JsonResponse({'ok': True, 'available': False, 'reason': 'empty'})
    exists = Team.objects.filter(project=project, name=name).exists()
    return JsonResponse({'ok': True, 'available': not exists})


@login_required
@require_http_methods(['GET'])
def new_team_info(request, project_id=None):
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    name = (request.GET.get('name') or '').strip()
    team = Team.objects.filter(project=project, name=name).first()
    if not team:
        team = Team.objects.filter(project=project, visible_name__iexact=name).first()
    if not team:
        return JsonResponse({'ok': True, 'exists': False})
    return JsonResponse({'ok': True, 'exists': True, 'n_users': team.get_n_users_on(), 'visible_name': team.visible_name})


@login_required
@require_http_methods(['GET', 'POST'])
def new_team_create(request, project_id=None):
    if not has_profile(request.user):
        raise Http404()
    if request.method == 'GET':
        ctx = _new_team_ui_context(request)
        ctx['team_section'] = 'create'
        ctx['page_title'] = 'Создать команду'
        return render(request, 'ui/team.html', ctx)
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
    request.user.profile.add_team_membership(team, make_primary=_post_make_new_team_primary(request))
    request.user.profile.team_requested = None
    request.user.profile.join_accept_as_primary = True
    request.user.profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
    return _team_redirect(request)


@login_required
@require_http_methods(['POST'])
def new_team_request_join(request, project_id=None):
    if not has_profile(request.user) or request.user.profile.team_requested:
        raise Http404()
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    name = (request.POST.get('name') or '').strip()
    team = Team.objects.filter(project=project, name=name).first()
    if not team:
        team = Team.objects.filter(project=project, visible_name__iexact=name).first()
    if not team:
        raise Http404()
    if ProfileTeamMembership.objects.filter(profile=request.user.profile, team=team).exists():
        messages.error(request, 'Вы уже в этой команде.')
        return _team_join_redirect(request)
    profile = request.user.profile
    profile.join_accept_as_primary = _post_make_new_team_primary(request)
    profile.team_requested = team
    profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
    return _team_redirect(request)


def _team_join_password_matches(stored, provided):
    """Case-insensitive compare; join codes are hex from secrets.token_hex (lowercase)."""
    if not stored or not provided:
        return False
    a = stored.strip().lower().encode('utf-8')
    b = provided.strip().lower().encode('utf-8')
    return hmac.compare_digest(a, b)


@login_required
@require_http_methods(['POST'])
def new_team_join_by_password(request, project_id=None):
    if not has_profile(request.user):
        messages.error(request, 'Нельзя вступить в команду сейчас.')
        return _team_join_redirect(request)
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    name = (request.POST.get('name') or '').strip()
    password = (request.POST.get('password') or '').strip()
    team = Team.objects.filter(project=project, name=name).first()
    if not team:
        team = Team.objects.filter(project=project, visible_name__iexact=name).first()
    if not team:
        messages.error(request, 'Команда не найдена.')
        return _team_join_redirect(request)
    stored = (team.join_password or '').strip()
    if not stored:
        messages.error(
            request,
            'У команды не задан код для быстрого входа. Капитан может задать его на странице команды.',
        )
        return _team_join_redirect(request)
    if not password or not _team_join_password_matches(stored, password):
        messages.error(request, 'Неверный пароль.')
        return _team_join_redirect(request)
    if ProfileTeamMembership.objects.filter(profile=request.user.profile, team=team).exists():
        messages.info(request, 'Вы уже в этой команде.')
        return _team_join_redirect(request)
    request.user.profile.add_team_membership(team, make_primary=_post_make_new_team_primary(request))
    request.user.profile.team_requested = None
    request.user.profile.join_accept_as_primary = True
    request.user.profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
    messages.success(request, 'Вы вступили в команду.')
    return _team_redirect(request)


@login_required
@require_http_methods(['POST'])
def new_team_set_primary(request, project_id=None):
    if not has_profile(request.user):
        raise Http404()
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    team_pk = (request.POST.get('team') or '').strip()
    team = get_object_or_404(Team, pk=team_pk)
    if team.project_id != project.id:
        raise Http404()
    if not request.user.profile.set_primary_team(team):
        messages.error(request, 'Нет доступа к этой команде.')
    return _team_redirect(request)


@login_required
@require_http_methods(['GET', 'POST'])
def new_team_password(request, project_id=None):
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
def new_team_rename(request, project_id=None):
    if not has_profile(request.user) or not request.user.profile.team_on:
        raise Http404()
    team = request.user.profile.team_on
    visible_name = (request.POST.get('visible_name') or '').strip()
    if not visible_name:
        messages.error(request, 'Название не может быть пустым.')
        return _team_redirect(request)
    team.visible_name = visible_name
    team.save(update_fields=['visible_name'])
    messages.success(request, 'Название команды обновлено.')
    return _team_redirect(request)
