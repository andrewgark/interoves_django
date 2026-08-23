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
from django.conf import settings
from django.forms import ChoiceField, ModelForm, TextInput
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html, strip_tags
from django.utils.safestring import mark_safe
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from games.forms import CreateTeamForm, JoinTeamForm
from games.daily_transitions import (
    next_daily_content_transition_for_game,
    next_daily_content_transition_for_games,
)

from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from allauth.socialaccount.models import SocialAccount

from games.access import game_has_started
from games.analytics import (
    YANDEX_GOAL_TICKET_CHECKOUT,
    analytics_ack_payload,
    ticket_purchase_goal_payload,
    yandex_goal_payload,
)
from games.alphabetty_daily import ALPHABETTY_GAME_ID
from games.daily_section import (
    current_number_for,
    filter_published_links,
    is_scheduled_game,
    publish_at_for,
    scheduled_number_is_public,
    uses_daily_play_layout,
    visible_links,
)
from games.ladder_daily import (
    LADDER_GAME_ID,
    get_ladder_hub_context,
    is_ladder_number_published,
)
from games.week_task_weekly import WEEK_TASK_GAME_ID
from games.ladder_word_results import (
    build_ladder_word_results_context,
    ladder_word_results_headers_context,
)
from games.section_hub import (
    HUB_DAILY_SECTION_IDS,
    HUB_FROM_DESYATOCHKI_SECTION_IDS,
    SECTION_HUB_META,
    SECTION_HUB_ORDER,
    WEEK_TASK_HUB_ID,
    get_desyatochki_hub_context,
    get_scheduled_section_hub_card,
    get_source_desyatka_context,
    get_training_section_hub_context,
    get_week_task_hub_card,
    section_format_credit_context,
    section_nav_title,
)
from games.grid_puzzle import GridPuzzleDataError, public_grid_puzzle_context
from games.week_task_pool import source_play_path_from_tags, source_summary_from_tags
from games.task_titles import task_display_name, task_group_page_title
from games.models import (
    Attempt,
    AudioManager,
    BugReport,
    ChainTaskState,
    Donation,
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
    StatisticsEvent,
    Task,
    TaskGroup,
    Team,
    TicketRequest,
)
from games.models import GameResultsSnapshot, TICKET_REQUESTS_PAGE_SIZE
from games.util import clean_text
from games.replacements_lines import canonical_replacements_checker_line, parse_replacements_lines_text
from games.raddle import (
    build_raddle_ui_context,
    load_raddle_state,
    parse_raddle_data,
    raddle_hub_result_for_actor,
    raddle_word_solved_list,
)
from games.word_salad import (
    WORD_SALAD_GAME_ID,
    archive_card_meta as word_salad_archive_card_meta,
    build_ui_context as build_word_salad_ui_context,
    load_state as load_word_salad_state,
    parse_task_data as parse_word_salad_task_data,
    salad_hub_result_for_actor,
)
from games.share_result import share_host_from_request
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
from games.results_snapshot import (
    get_live_results_payload,
    results_attempts_scope_game,
    snapshot_headers_context,
    snapshot_to_results_context,
)
from games.nowpayments_util import create_invoice as nowpayments_create_invoice
from games.nowpayments_util import embed_url_for_invoice
from games.nowpayments_util import nowpayments_ipn_callback_url
from games.payment_routes import (
    CRYPTO,
    INTERNATIONAL_CARD,
    RUSSIAN_CARD,
    amount_for as ticket_amount_for,
    route_for as ticket_route_for,
    unit_price_for as ticket_unit_price_for,
)
from games.yookassa_util import configure_yookassa_from_env

from yookassa import Payment

logger = logging.getLogger(__name__)


def _anon_key_from_request(request):
    """Идентификатор анонимного игрока в личном режиме: ?anon= / ?anon_key=, cookie interoves_anon или X-Interoves-Anon."""
    if request.user.is_authenticated:
        return None
    return (
        request.GET.get('anon')
        or request.GET.get('anon_key')
        or request.COOKIES.get('interoves_anon')
        or request.headers.get('X-Interoves-Anon')
    )


def _age_gate_context(game, task_group=None, *, back_url='/'):
    """Client-side 18+ gate: localStorage key + back link for the leave button."""
    game_flag = bool(getattr(game, 'is_18_plus', False))
    tg_flag = bool(task_group and getattr(task_group, 'is_18_plus', False))
    if not game_flag and not tg_flag:
        return {}
    if game_flag:
        storage_key = 'interoves_18plus_game_{}'.format(game.id)
    else:
        storage_key = 'interoves_18plus_tg_{}'.format(task_group.id)
    return {
        'show_18plus_gate': True,
        'age_gate_storage_key': storage_key,
        'age_gate_back_url': back_url,
    }


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


def _game_task_group_links(game):
    return GameTaskGroup.sorted_links(
        GameTaskGroup.objects.filter(game=game)
        .select_related('task_group')
        .annotate(n_tasks=Count('task_group__tasks', filter=Q(task_group__tasks__is_removed=False)))
    )


def _player_visible_task_group_links(game):
    """Круги, видимые игроку (для расписанных разделов — только уже вышедшие)."""
    links = _game_task_group_links(game)
    if is_scheduled_game(game.id):
        return visible_links(links, game, reverse=True)
    return links


def _neighbors_by_pk(links, placement):
    """Предыдущая/следующая ссылка в упорядоченном списке по pk placement."""
    links = list(links)
    pks = [l.pk for l in links]
    try:
        idx = pks.index(placement.pk)
    except ValueError:
        return None, None
    prev_l = links[idx - 1] if idx > 0 else None
    next_l = links[idx + 1] if idx + 1 < len(links) else None
    return prev_l, next_l


def _resolve_game_page_actor(request, play_mode):
    """Team/user/anon_key for personal progress on game hub pages."""
    team = user = anon_key = None
    if play_mode == 'team':
        if has_profile(request.user):
            team = request.user.profile.team_on
    else:
        if request.user.is_authenticated and has_profile(request.user):
            user = request.user
        else:
            anon_key = _anon_key_from_request(request)
    return team, user, anon_key


def _play_url_for_task_group(game, number, *, project_base=''):
    if project_base:
        return '{}/games/{}/{}/'.format(project_base, game.id, number)
    from games.section_paths import is_root_section_game, section_play_path
    if is_root_section_game(game.id):
        return section_play_path(game.id, number)
    return '/games/{}/{}/'.format(game.id, number)


def _task_group_results_url(game, number, *, project_base=''):
    """Canonical results URL for one task group, alongside its play URL."""
    if project_base:
        return '{}/games/{}/{}/results/'.format(project_base, game.id, number)
    from games.section_paths import is_root_section_game, section_play_path
    if is_root_section_game(game.id):
        return '{}results/'.format(section_play_path(game.id, number))
    return '/games/{}/{}/results/'.format(game.id, number)


def _task_group_page_nav_context(game, *, prev_tg=None, next_tg=None):
    """Подписи верхнего «назад к списку» и нижнего пейджера кругов."""
    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        back_label = 'К списку'
    elif game.project_id == NEW_UI_PROJECT:
        back_label = 'К игре'
    else:
        back_label = 'Назад'

    section_meta = SECTION_HUB_META.get(game.id) or {}
    if section_meta.get('pager_label'):
        pager_label = section_meta['pager_label']
        pager_aria_label = (
            section_meta.get('pager_aria_label')
            or 'Переход между заданиями «{}»'.format(pager_label)
        )
        results_label = section_meta.get('results_label') or 'Результаты'
    else:
        raw_label = (
            section_meta.get('title')
            or game.no_html_name
            or game.outside_name
            or game.name
            or 'Задание'
        )
        pager_label = strip_tags(str(raw_label)).strip() or 'Задание'
        pager_aria_label = 'Переход между заданиями «{}»'.format(pager_label)
        results_label = 'Результаты'
    return {
        'back_label': back_label,
        'task_group_pager_label': pager_label,
        'task_group_pager_aria_label': pager_aria_label,
        'task_group_results_label': results_label,
        'prev_task_group_number': prev_tg.number if prev_tg else None,
        'prev_task_group_name': prev_tg.name if prev_tg else None,
        'next_task_group_number': next_tg.number if next_tg else None,
        'next_task_group_name': next_tg.name if next_tg else None,
    }


def _task_group_rows_skeleton(task_groups, game, *, project_base=''):
    """Task group list for game hub pages; actor progress is loaded separately."""
    return [
        {
            'task_group': p.task_group,
            'game': game,
            'number': p.number,
            'n_tasks': p.n_tasks,
            'n_solved': None,
            'play_url': _play_url_for_task_group(game, p.number, project_base=project_base),
            'results_url': _task_group_results_url(game, p.number, project_base=project_base),
            'is_fully_solved': False,
            'row_class': '',
            'title': '{} · {}'.format(p.number, p.name),
            'progress_text': None,
        }
        for p in task_groups
    ]


def _task_group_progress_payload(game, task_groups, *, team=None, user=None, anon_key=None, mode='general'):
    canonical_groups = [p.task_group for p in task_groups]
    solved_task_ids, tg_to_task_ids = _compute_solved_task_ids(
        game=game,
        task_groups=canonical_groups,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
    )
    result_squares_by_number = {}
    elapsed_by_number = {}
    has_actor = team is not None or user is not None or anon_key is not None
    if has_actor and game.id in (LADDER_GAME_ID, WORD_SALAD_GAME_ID):
        tg_ids = [tg.id for tg in canonical_groups]
        include_other_games = game.project_id == NEW_UI_SECTIONS_PROJECT
        if game.id == LADDER_GAME_ID:
            tasks = list(
                Task.objects.filter(task_group_id__in=tg_ids, task_type='raddle').visible()
            )
            hub_result = raddle_hub_result_for_actor
            hub_kwargs = {'allow_partial': True}
        else:
            tasks = list(
                Task.objects.filter(task_group_id__in=tg_ids, task_type='word_salad').visible()
            )
            hub_result = salad_hub_result_for_actor
            hub_kwargs = {}
        tg_to_task = {}
        for task in tasks:
            tg_to_task.setdefault(task.task_group_id, task)
        for p in task_groups:
            task = tg_to_task.get(p.task_group_id)
            if not task:
                continue
            squares, elapsed = hub_result(
                task,
                team=team,
                user=user,
                anon_key=anon_key,
                mode=mode,
                game=game,
                include_other_games=include_other_games,
                **hub_kwargs,
            )
            if squares:
                result_squares_by_number[str(p.number)] = squares
            if elapsed:
                elapsed_by_number[str(p.number)] = elapsed
    rows = {}
    for p in task_groups:
        tg = p.task_group
        tg_task_ids = tg_to_task_ids.get(tg.id, [])
        n_solved = len([tid for tid in tg_task_ids if tid in solved_task_ids])
        row_class = ''
        if p.n_tasks and n_solved >= p.n_tasks:
            row_class = 'new-task--solved'
        elif n_solved:
            row_class = 'new-task--partial'
        is_fully_solved = bool(p.n_tasks) and n_solved >= p.n_tasks
        # Пишем «N из M решено» только при частичном прогрессе (0 < N < M).
        if n_solved and p.n_tasks and n_solved < p.n_tasks:
            progress_text = '{} из {} {} решено'.format(
                n_solved, p.n_tasks, _ru_iz_punkt_word(p.n_tasks),
            )
        else:
            progress_text = None
        # Лесенка/салатик: квадраты. Полные → зелёный; с ⬜ → жёлтый partial.
        result_squares = result_squares_by_number.get(str(p.number))
        elapsed_label = elapsed_by_number.get(str(p.number))
        if result_squares:
            progress_text = None
            if '⬜' in result_squares:
                is_fully_solved = False
                row_class = 'new-task--partial'
                elapsed_label = None
            else:
                is_fully_solved = True
                row_class = 'new-task--solved'
                if p.n_tasks:
                    n_solved = max(n_solved, p.n_tasks)
        elif not is_fully_solved:
            result_squares = None
            elapsed_label = None
        rows[str(p.number)] = {
            'n_solved': n_solved,
            'n_tasks': p.n_tasks,
            'is_fully_solved': is_fully_solved,
            'row_class': row_class,
            'progress_text': progress_text,
            'result_squares': result_squares,
            'elapsed_label': elapsed_label,
        }
    return rows


def _game_page_progress_context(request, game, play_mode):
    """Template flags/URL for lazy per-actor progress on game hub pages."""
    team, user, anon_key = _resolve_game_page_actor(request, play_mode)
    load = (play_mode == 'team' and team is not None) or (
        play_mode != 'team' and (user is not None or anon_key is not None)
    )
    url = None
    if load:
        from games.section_paths import is_root_section_game, section_progress_path
        # Sections and main games use site-root URLs; other projects are scoped.
        if game.project_id not in (NEW_UI_PROJECT, NEW_UI_SECTIONS_PROJECT):
            url = reverse('project_game_progress', kwargs={
                'project_id': game.project_id,
                'game_id': game.id,
            })
        elif is_root_section_game(game.id):
            url = section_progress_path(game.id)
        else:
            url = reverse('ui_game_progress', kwargs={'game_id': game.id})
    return {
        'load_task_group_progress': load,
        'task_group_progress_url': url,
    }


def _game_task_group_progress_response(request, game):
    team_for_access = request.user.profile.team_on if has_profile(request.user) else None
    if not game.has_access('see_game_preview', team=team_for_access):
        raise Http404()
    if game.project_id != NEW_UI_SECTIONS_PROJECT and not game_has_started(game):
        raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    team, user, anon_key = _resolve_game_page_actor(request, play_mode)
    if play_mode == 'team' and team is None:
        return JsonResponse({'rows': {}})
    if play_mode != 'team' and user is None and anon_key is None:
        return JsonResponse({'rows': {}})

    task_groups = _player_visible_task_group_links(game)
    mode = game.get_current_mode(Attempt(time=timezone.now()))
    rows = _task_group_progress_payload(
        game,
        task_groups,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
    )
    return JsonResponse({'rows': rows})


def new_game_task_group_progress(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    return _game_task_group_progress_response(request, game)


def project_game_task_group_progress(request, project_id, game_id):
    project = get_object_or_404(Project, id=(project_id or '').strip())
    game = get_object_or_404(Game, id=game_id, project=project)
    return _game_task_group_progress_response(request, game)

NEW_UI_PROJECT = 'main'
NEW_UI_SECTIONS_PROJECT = 'sections'
PALINDROMES_GAME_ID = 'palindromes'
# Разделы с собственным туториалом (модалка правил)
SECTION_RULES_GAME_IDS = (
    'palindromes', 'replacements', 'walls', 'ladder', 'alphabetty', 'week_task', WORD_SALAD_GAME_ID,
)


def _section_tutorial_html_for_game(game):
    """Rules HTML for the ? modal on a section play page.

    Hub pages read ``Game.section_default_rules``. Play pages used to look up
    only ``section_tutorial_<game.id>``. After salad was renamed from
    ``word_salad``, that convention no longer matches the stored HTMLPage
    (``section_tutorial_word_salad``), so the modal opened empty.
    """
    try:
        related = game.section_default_rules
    except HTMLPage.DoesNotExist:
        related = None
    if related is not None and (related.html or '').strip():
        return related.html
    names = []
    if game.id in SECTION_RULES_GAME_IDS:
        names.append('section_tutorial_' + game.id)
    related_id = getattr(game, 'section_default_rules_id', None)
    if related_id and related_id not in names:
        names.append(related_id)
    if not names:
        return None
    pages = {
        page.name: page.html or ''
        for page in HTMLPage.objects.filter(name__in=names)
    }
    for name in names:
        html = pages.get(name) or ''
        if html.strip():
            return html
    return None


def _project_base(project_id: str | None) -> str:
    """
    URL base prefix for project-scoped UI.

    - main project lives at site root -> ""
    - other projects live under "/<project_id>" -> "/glowbyte"
    """
    pid = (project_id or '').strip()
    # "sections" is a DB project for hub tiles (/section/<id>/), not a URL prefix like /glowbyte/.
    if not pid or pid in (NEW_UI_PROJECT, NEW_UI_SECTIONS_PROJECT):
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
        'ui_project_reports_url': (base + '/profile/reports/') if base else '/profile/reports/',
        'ui_project_pay_url': (base + '/pay/') if base else '/pay/',
    }


def _section_ui_context(game):
    """Common section flags and URLs; format-specific views add their own extras."""
    is_section = getattr(game, 'project_id', None) == NEW_UI_SECTIONS_PROJECT
    if not is_section:
        return {}
    from games.section_paths import section_results_path

    return {
        'is_ladder_section': game.id == LADDER_GAME_ID,
        'is_alphabetty_section': game.id == ALPHABETTY_GAME_ID,
        'section_results_url': section_results_path(game.id),
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


def _reports_list_redirect(request):
    scoped = _scoped_project_id(request)
    if scoped:
        return redirect('project_profile_reports', project_id=scoped)
    return redirect('new_profile_reports')


def _report_detail_redirect(request, report_id):
    scoped = _scoped_project_id(request)
    if scoped:
        return redirect('project_profile_report_detail', project_id=scoped, report_id=report_id)
    return redirect('new_profile_report_detail', report_id=report_id)


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


# Same order as hub "Задания из Десяточек" (week_task → replacements → walls → palindromes).
_SECTION_NAV_ORDER = {
    game_id: i for i, game_id in enumerate(HUB_FROM_DESYATOCHKI_SECTION_IDS)
}


def get_section_games(request):
    """Игры из project 'sections' с доступом на превью (навигация; без лесенки/алфавитки)."""
    project = Project.objects.filter(id=NEW_UI_SECTIONS_PROJECT).first()
    if not project:
        return []
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    games_list = [
        g for g in Game.objects.filter(project=project)
        if g.id not in HUB_DAILY_SECTION_IDS
        and g.has_access('see_game_preview', team=team)
    ]
    return sorted(games_list, key=lambda g: _SECTION_NAV_ORDER.get(g.id, 99))


def _hub_section_task_group_links(game):
    """Круги раздела для списка на странице — новые сверху."""
    qs = (
        GameTaskGroup.objects.filter(game=game)
        .select_related('task_group')
        .annotate(n_tasks=Count('task_group__tasks', filter=Q(task_group__tasks__is_removed=False)))
    )
    if is_scheduled_game(game.id):
        return visible_links(qs, game, reverse=True)
    return GameTaskGroup.order_queryset_by_number(qs, reverse=True)


def _build_hub_section_cards(request, *, team):
    """Карточки разделов на главной, сгруппированные для двух блоков."""
    project = Project.objects.filter(id=NEW_UI_SECTIONS_PROJECT).first()
    by_id = {}
    if project:
        for game_id in SECTION_HUB_ORDER:
            game = Game.objects.filter(id=game_id, project=project).first()
            if not game or not game.has_access('see_game_preview', team=team):
                continue
            if is_scheduled_game(game_id):
                card = get_scheduled_section_hub_card(
                    game,
                    published_numbers=_published_numbers(game),
                )
            else:
                card = get_training_section_hub_context(game)
            play_mode, _ = _get_play_mode(request, game.project_id)
            play_mode = effective_play_mode(play_mode, game, user=request.user)
            card.update(_game_page_progress_context(request, game, play_mode))
            by_id[game_id] = card

    week_game = (
        Game.objects.filter(id=WEEK_TASK_GAME_ID, project_id=NEW_UI_SECTIONS_PROJECT)
        .first()
        if project
        else None
    )
    if week_game and week_game.has_access('see_game_preview', team=team):
        card = get_scheduled_section_hub_card(
            week_game,
            published_numbers=_published_numbers(week_game),
        )
        play_mode, _ = _get_play_mode(request, week_game.project_id)
        play_mode = effective_play_mode(play_mode, week_game, user=request.user)
        card.update(_game_page_progress_context(request, week_game, play_mode))
        by_id[WEEK_TASK_HUB_ID] = card
    else:
        by_id[WEEK_TASK_HUB_ID] = get_week_task_hub_card()

    daily = [by_id[i] for i in HUB_DAILY_SECTION_IDS if i in by_id]
    from_desyatochki = [
        by_id[i] for i in HUB_FROM_DESYATOCHKI_SECTION_IDS if i in by_id
    ]
    return {
        'daily_hub_cards': daily,
        'from_desyatochki_hub_cards': from_desyatochki,
        'hub_section_cards': daily + from_desyatochki,
    }


def _get_ladder_game():
    return (
        Game.objects.filter(id=LADDER_GAME_ID, project_id=NEW_UI_SECTIONS_PROJECT)
        .first()
    )


def _sections_hub_url(game_id):
    """Canonical public URL for a sections-project game hub."""
    from games.section_paths import section_hub_path
    return section_hub_path(game_id)


def _published_numbers(game):
    return {link.number for link in filter_published_links(_game_task_group_links(game), game)}


def _ladder_published_numbers(game):
    return _published_numbers(game)


def _ladder_latest_play_url(game):
    """URL of the newest published ladder, or None if none exist yet."""
    numbers = [
        int(n) for n in _published_numbers(game)
        if str(n).isdigit() and scheduled_number_is_public(game, n)
    ]
    if not numbers:
        return None
    return _play_url_for_task_group(game, max(numbers))


def _ladder_task_group_rows(
    task_groups,
    game,
    *,
    today_number=None,
    today_prefix='Сегодня',
    item_label='Лесенка',
):
    task_groups = list(task_groups)
    endpoints_by_task_group = {}
    if game.id == LADDER_GAME_ID:
        task_group_ids = [p.task_group_id for p in task_groups]
        raddle_tasks = (
            Task.objects.filter(
                task_group_id__in=task_group_ids,
                task_type='raddle',
            )
            .visible()
            .only('id', 'task_group_id', 'task_type', 'checker_data', 'answer')
            .order_by('task_group_id', 'id')
        )
        for task in raddle_tasks:
            if task.task_group_id in endpoints_by_task_group:
                continue
            parsed = parse_raddle_data(task)
            if not parsed or not parsed['words']:
                continue
            first_word = parsed['words'][0]
            last_word = parsed['words'][-1]
            if first_word and last_word:
                endpoints_by_task_group[task.task_group_id] = (first_word, last_word)

    salad_meta_by_task_group = {}
    if game.id == WORD_SALAD_GAME_ID:
        salad_tasks = (
            Task.objects.filter(
                task_group_id__in=[p.task_group_id for p in task_groups],
                task_type='word_salad',
            )
            .visible()
            .only('id', 'task_group_id', 'checker_data', 'text')
            .order_by('task_group_id', 'id')
        )
        for task in salad_tasks:
            if task.task_group_id in salad_meta_by_task_group:
                continue
            salad_meta_by_task_group[task.task_group_id] = word_salad_archive_card_meta(task)

    rows = []
    for p in task_groups:
        is_today = today_number is not None and str(p.number) == str(today_number)
        title = (
            '{} · {} №{}'.format(today_prefix, item_label, p.number)
            if is_today
            else '{} №{}'.format(item_label, p.number)
        )
        endpoints = endpoints_by_task_group.get(p.task_group_id)
        rows.append({
            'task_group': p.task_group,
            'game': game,
            'number': p.number,
            'n_tasks': p.n_tasks,
            'n_solved': None,
            'play_url': _play_url_for_task_group(game, p.number),
            'results_url': _task_group_results_url(game, p.number),
            'is_fully_solved': False,
            'row_class': 'new-task--today' if is_today else '',
            'title': title,
            'progress_text': None,
            'raddle_start_word': endpoints[0] if endpoints else None,
            'raddle_end_word': endpoints[1] if endpoints else None,
            'salad_meta': salad_meta_by_task_group.get(p.task_group_id),
        })
    return rows


def _folder_by_slug(slug):
    for f in NEW_UI_FOLDERS:
        if f['slug'] == slug:
            return f
    return None


def new_hub(request):
    project = Project.objects.filter(id=NEW_UI_PROJECT).first()
    section_games = get_section_games(request)
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    hub_groups = _build_hub_section_cards(request, team=team)
    live_next_transition_at = next_daily_content_transition_for_games(
        card.get('game') for card in hub_groups['daily_hub_cards']
    )
    view = MainPageView()
    view.project_name = NEW_UI_PROJECT
    desyatochki_games = view.get_games_list(request)
    # Карточку десяточек показываем только если есть хотя бы одна видимая игра.
    desyatochki_card = get_desyatochki_hub_context(desyatochki_games) if desyatochki_games else None
    return render(request, 'ui/hub.html', {
        'project': project,
        'section_games': section_games,
        **hub_groups,
        'desyatochki_card': desyatochki_card,
        **_games_list_card_context(request),
        'page_title': 'Interoves',
        'show_sections_nav': True,
        'live_next_transition_at': live_next_transition_at,
        # Кнопка «Купить билеты» — только на главной (не в /glowbyte/ и др.).
        'show_desyatochki_pay_cta': True,
        # Баннер «Для компаний» — только на главной странице.
        'show_order_banner': True,
        # Чат участников в блоке десяточек — только на главной.
        'desyatochki_participants_chat_url': 'https://t.me/+rhsbkEuU4-ExOWEy',
        'community_links': [
            {'kind': 'telegram', 'title': 'Телеграм-канал', 'href': 'https://t.me/interoves'},
            {'kind': 'twitter', 'title': 'X (Twitter)', 'href': 'https://x.com/interoves'},
            {'kind': 'instagram', 'title': 'Instagram', 'href': 'https://www.instagram.com/interoveslocumpraesta/'},
            {'kind': 'telegram', 'title': 'Чат участников', 'href': 'https://t.me/+rhsbkEuU4-ExOWEy'},
            {'kind': 'telegram', 'title': 'Чат решающих PuzzleHunts', 'href': 'https://t.me/+GPR22w8MdLEyNzIy'},
            {'kind': 'telegram', 'title': 'Разработчик: Андрей', 'href': 'https://t.me/andrewgark'},
        ],
        'interesting_links': [
            {
                'kind': 'nutrimatic',
                'title': 'Nutrimatic',
                'note': 'поиск слов по маске',
                'href': '/nutrimatic-ru/',
            },
            {
                'kind': 'eurovision',
                'title': 'Буклеты к Евровидению',
                'note': 'красивые',
                'href': '/eurovision_booklet/',
            },
            {'kind': 'vpn', 'title': 'VPN от наших друзей', 'href': '/vpn/'},
        ],
        'show_donate_cta': True,
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


def _team_for_access(request):
    """Активная команда для access-проверок (None для гостя / без команды)."""
    if not request.user.is_authenticated or not has_profile(request.user):
        return None
    if not has_team(request.user):
        return None
    return request.user.profile.team_on


def _registration_blocks_play(game, team):
    """Нужна регистрация на игру, а у team её нет (включая team=None)."""
    return bool(
        game.has_access('needs_registration', team=team)
        and not game.has_access('is_registered', team=team)
    )


def _announced_game_page_response(request, game, *, back_url, show_sections_nav=True, project=None):
    """Карточка игры из списка десяточек (анонс / нет доступа к заданиям без регистрации)."""
    page_title = game.get_outside_name() if hasattr(game, 'get_outside_name') else (game.outside_name or game.name)
    ctx = {
        'game': game,
        'games': [game],
        'page_title': page_title,
        'back_url': back_url,
        'show_sections_nav': show_sections_nav,
        **_games_list_card_context(request),
        **_project_urls_context(game.project_id),
        **_age_gate_context(game, back_url=back_url),
    }
    if project is not None:
        ctx['project'] = project
    return render(request, 'ui/game_announce_page.html', ctx)


def _maybe_registration_or_announce_response(
    request,
    game,
    *,
    back_url,
    show_sections_nav=True,
    project=None,
    team=None,
):
    """
    Для обычных игр (не sections): карточка анонса вместо заданий, если игра ещё
    не началась или команде нужна регистрация. None — можно показывать задания.
    """
    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        return None
    if team is None:
        team = _team_for_access(request)
    if not game.has_access('see_game_preview', team=team):
        raise Http404()
    if not game_has_started(game) or _registration_blocks_play(game, team):
        return _announced_game_page_response(
            request,
            game,
            back_url=back_url,
            show_sections_nav=show_sections_nav,
            project=project,
        )
    return None


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
    view = MainPageView()
    view.project_name = project.id
    project_games = view.get_games_list(request)
    # Карточку десяточек показываем только если есть хотя бы одна видимая игра.
    desyatochki_card = get_desyatochki_hub_context(project_games, base=base) if project_games else None
    return render(request, 'ui/hub.html', {
        'project': project,
        'desyatochki_card': desyatochki_card,
        'section_games': [],
        'page_title': project.id,
        'show_sections_nav': False,
        **_games_list_card_context(request),
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
    if not request.user.is_authenticated and not personal_play_mode_locked(game, user=request.user):
        play_mode = 'personal'
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    has_profile_user = has_profile(request.user)
    team = _team_for_access(request)

    games_back = (base + '/games/') if base else '/games/'
    gate = _maybe_registration_or_announce_response(
        request,
        game,
        back_url=games_back,
        show_sections_nav=False,
        project=project,
        team=team,
    )
    if gate is not None:
        return gate

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

    task_groups = _game_task_group_links(game)
    task_group_rows = _task_group_rows_skeleton(task_groups, game, project_base=base)

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
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'section_games': [],
        'show_sections_nav': False,
        **_project_urls_context(project.id),
        **_game_page_progress_context(request, game, play_mode),
        **_age_gate_context(game, back_url=(base + '/games/') if base else '/games/'),
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
    play_mode = effective_play_mode(play_mode, game, user=request.user)
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
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
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
        'play_mode': effective_play_mode(
            _get_play_mode(request, game.project_id)[0], game, user=request.user,
        ),
        'play_mode_project_id': game.project_id,
        'page_title': 'Результаты турнира: {}'.format(game.get_no_html_name() if hasattr(game, 'get_no_html_name') else game.name),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'section_games': [],
        'show_sections_nav': False,
        **_project_urls_context(project.id),
    })


def project_task_group_page(request, project_id, game_id, task_group_number):
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)
    base = _project_base(project.id)
    game = get_object_or_404(Game, id=game_id, project=project)

    games_back = (base + '/games/') if base else '/games/'
    gate = _maybe_registration_or_announce_response(
        request,
        game,
        back_url=games_back,
        show_sections_nav=False,
        project=project,
    )
    if gate is not None:
        return gate

    play_mode, play_mode_key = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    anon_key = None

    if not request.user.is_authenticated:
        if personal_play_mode_locked(game, user=request.user):
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

    if play_mode == 'team':
        if not game.has_access('play', team=team):
            raise Http404()
    else:
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise Http404()
        if not game.is_playable:
            raise Http404()

    mode = game.get_current_mode(Attempt(time=timezone.now()))
    placement = (
        GameTaskGroup.objects.select_related('task_group', 'task_group__rules')
        .filter(game=game, number=str(task_group_number))
        .first()
    )
    if not placement:
        fallback = GameTaskGroup.nearest_by_number(game, task_group_number)
        if fallback:
            return redirect('project_task_group', project_id=project.id, game_id=game.id, task_group_number=fallback.number)
        raise Http404()
    task_group = placement.task_group
    prev_tg, next_tg = GameTaskGroup.prev_next_for(game, placement)
    tasks = sorted(task_group.tasks.visible(), key=lambda t: t.key_sort())
    ctx_dicts = build_task_group_task_context_dicts(
        game, task_group, tasks, team, user, anon_key, mode, placement=placement,
    )
    return render(request, 'ui/task_group.html', {
        'project': project,
        'game': game,
        'task_group': task_group,
        'tasks': tasks,
        'attempts_info_by_task_id': ctx_dicts['attempts_info_by_task_id'],
        'replacements_lines_data': ctx_dicts['replacements_lines_data'],
        'word_salad_data': ctx_dicts['word_salad_data'],
        'raddle_data': ctx_dicts['raddle_data'],
        'proportions_chips': ctx_dicts['proportions_chips'],
        'wall_max_points_meta_by_task_id': ctx_dicts['wall_max_points_meta_by_task_id'],
        'likes_meta_by_task_id': ctx_dicts['likes_meta_by_task_id'],
        'task_ui_by_task_id': ctx_dicts['task_ui_by_task_id'],
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
        'task_group_results_url': _task_group_results_url(game, placement.number, project_base=base),
        'task_group_results_allowed': game.has_access('see_results', mode='general', team=team),
        'tg_number': placement.number,
        'tg_name': placement.name,
        'share_host': share_host_from_request(request),
        'back_url': '{}/games/{}/'.format(base, game.id),
        **_task_group_page_nav_context(game, prev_tg=prev_tg, next_tg=next_tg),
        'page_title': '{} · {}'.format(game.outside_name or game.name, placement.name),
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'section_games': [],
        'show_sections_nav': False,
        **_project_urls_context(project.id),
        **_age_gate_context(
            game,
            task_group=task_group,
            back_url='{}/games/{}/'.format(base, game.id),
        ),
    })


def new_ladder_today_page(request):
    """Редирект на сегодняшнюю (или последнюю опубликованную) лесенку."""
    ladder_game = _get_ladder_game()
    if not ladder_game:
        raise Http404()
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    if not ladder_game.has_access('see_game_preview', team=team):
        raise Http404()
    ctx = get_ladder_hub_context(
        ladder_game,
        published_numbers=_ladder_published_numbers(ladder_game),
    )
    cta_number = ctx.get('ladder_cta_number')
    if not cta_number:
        return redirect('ui_ladder_hub')
    return redirect(_play_url_for_task_group(ladder_game, cta_number))


def new_ladder_last_page(request):
    """Редирект на последнюю опубликованную лесенку."""
    ladder_game = _get_ladder_game()
    if not ladder_game:
        raise Http404()
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    if not ladder_game.has_access('see_game_preview', team=team):
        raise Http404()
    play_url = _ladder_latest_play_url(ladder_game)
    if not play_url:
        return redirect('ui_ladder_hub')
    return redirect(play_url)


def new_section_last_page(request, game_id):
    """Редирект на последний круг раздела (walls / week_task / …)."""
    if game_id in (LADDER_GAME_ID, ALPHABETTY_GAME_ID):
        raise Http404()
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
    from games.section_hub import _newest_task_group_links
    from games.section_paths import section_hub_path, section_play_path
    links = list(_newest_task_group_links(game))
    if not links:
        return redirect(section_hub_path(game.id))
    return redirect(section_play_path(game.id, links[0].number))


def new_ladder_hub_page(request):
    """Архив лесенок: /ladder/ (канонический URL вместо /games/ladder/ и /section/ladder/)."""
    return _render_section_game_page(request, LADDER_GAME_ID)


def new_section_game_page(request, game_id):
    """Страница раздела (игра из project sections) в новом UI: правила при необходимости + список групп заданий."""
    if game_id == LADDER_GAME_ID:
        return redirect('ui_ladder_hub', permanent=True)
    if game_id == ALPHABETTY_GAME_ID:
        return redirect('ui_alphabetty_hub', permanent=True)
    return _render_section_game_page(request, game_id)


def _render_section_game_page(request, game_id):
    project = Project.objects.filter(id=NEW_UI_SECTIONS_PROJECT).first()
    if not project:
        raise Http404()
    game = (
        Game.objects.filter(project=project, id=game_id)
        .select_related('section_default_rules')
        .first()
    )
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
    play_mode, play_mode_key = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    meta = SECTION_HUB_META.get(game_id) or {}
    today_number = current_number_for(game)
    task_groups = _hub_section_task_group_links(game)
    if meta.get('archive_item_label'):
        task_group_rows = _ladder_task_group_rows(
            task_groups,
            game,
            today_number=today_number,
            today_prefix=meta.get('archive_today_prefix', 'Сегодня'),
            item_label=meta['archive_item_label'],
        )
        task_groups_heading = 'Архив'
        task_groups_empty_text = meta.get(
            'archive_empty_text',
            'Скоро появятся — следите за обновлениями.',
        )
    else:
        task_group_rows = _task_group_rows_skeleton(task_groups, game)
        task_groups_heading = _task_group_page_nav_context(game)['task_group_pager_label']
        task_groups_empty_text = 'В этом разделе пока нет заданий. Добавьте их в админке.'

    section_today_play_url = None
    section_today_cta_label = ''
    if (
        today_number is not None
        and scheduled_number_is_public(game, today_number)
        and meta.get('cta_today')
    ):
        has_today = any(str(p.number) == str(today_number) for p in task_groups)
        if has_today or game_id == LADDER_GAME_ID:
            from games.section_paths import section_last_path
            section_today_play_url = section_last_path(game_id)
            section_today_cta_label = meta['cta_today']

    section_task_groups_rules_html = None
    rules_page = game.section_default_rules
    if rules_page and (rules_page.html or '').strip():
        section_task_groups_rules_html = mark_safe((rules_page.html or '').strip())
    if section_task_groups_rules_html:
        section_rules_type = None
        section_tutorial_html = None
        show_palindrome_rules = False
    else:
        section_rules_type = game_id if game_id in SECTION_RULES_GAME_IDS else None
        section_tutorial_html = _section_tutorial_html_for_game(game)
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
        'task_groups_heading': task_groups_heading,
        'task_groups_empty_text': task_groups_empty_text,
        'section_tagline': meta.get('description') or '',
        'ladder_today_number': today_number if game_id == LADDER_GAME_ID else None,
        'section_today_play_url': section_today_play_url,
        'section_today_cta_label': section_today_cta_label,
        'alphabetty_create_url': (
            '/create_alphabetty/'
            if game_id == ALPHABETTY_GAME_ID
            else None
        ),
        'back_url': '/',
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'team': team_for_access,
        'show_sections_nav': True,
        'live_next_transition_at': next_daily_content_transition_for_game(game),
        **_project_urls_context(NEW_UI_PROJECT),
        **_section_ui_context(game),
        **_game_page_progress_context(request, game, play_mode),
        **_age_gate_context(game, back_url='/'),
    })


def new_main_game_page(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    if game.project_id != NEW_UI_PROJECT:
        raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    if not request.user.is_authenticated and not personal_play_mode_locked(game, user=request.user):
        play_mode = 'personal'
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    has_profile_user = has_profile(request.user)
    team = _team_for_access(request)

    # До старта или без регистрации — карточка анонса (кнопки Войти / Зарегаться / билеты).
    gate = _maybe_registration_or_announce_response(
        request,
        game,
        back_url='/games/',
        show_sections_nav=True,
        team=team,
    )
    if gate is not None:
        return gate

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

    task_groups = _game_task_group_links(game)
    task_group_rows = _task_group_rows_skeleton(task_groups, game)

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
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'show_sections_nav': True,
        **_project_urls_context(game.project_id),
        **_game_page_progress_context(request, game, play_mode),
        **_age_gate_context(game, back_url='/games/'),
    })


def _load_results_placements_and_tasks(game, task_group_number=None):
    """Placements + visible tasks for results table (headers and full compute)."""
    links = list(
        game.task_group_links.select_related('task_group').prefetch_related(
            Prefetch(
                'task_group__tasks',
                queryset=Task.objects.visible().filter(~Q(task_type='text_with_forms')),
                to_attr='result_tasks',
            )
        )
    )
    if is_scheduled_game(game.id):
        placements = visible_links(links, game, reverse=False)
    else:
        placements = sorted(links, key=lambda p: p.key_sort())
    if task_group_number is not None:
        placements = [p for p in placements if str(p.number) == str(task_group_number)]
    task_group_to_tasks = {}
    for p in placements:
        tg = p.task_group
        task_group_to_tasks[p.number] = sorted(
            getattr(tg, 'result_tasks', []) or [], key=lambda t: t.key_sort()
        )
    tasks_flat = [t for p in placements for t in task_group_to_tasks[p.number]]
    task_ids = [t.id for t in tasks_flat]
    task_group_headers = [
        _ResultsTaskGroupHeader(p.number, p.name, len(task_group_to_tasks[p.number]))
        for p in placements
    ]
    return placements, task_group_to_tasks, tasks_flat, task_ids, task_group_headers


def _results_table_headers_context(game, task_group_number=None):
    """Fast context for results table thead only."""
    _placements, task_group_to_tasks, _tasks_flat, _task_ids, task_group_headers = (
        _load_results_placements_and_tasks(game, task_group_number=task_group_number)
    )
    return {
        'task_groups': task_group_headers,
        'task_group_to_tasks': task_group_to_tasks,
    }


def _results_column_count(task_groups, mode='general'):
    """Return the actual number of columns in the shared results table."""
    fixed_columns = 4 if mode == 'tournament' else 3
    task_columns = sum(
        group.get_n_tasks_for_results() for group in (task_groups or [])
    )
    return fixed_columns + task_columns


def _results_rows_empty_context():
    return {
        'teams_sorted': [],
        'team_to_list_attempts_info': {},
        'team_to_cells': {},
        'team_to_score': {},
        'team_to_place': {},
        'team_to_max_best_time': {},
    }


def _load_game_results_data(game, mode):
    snap = GameResultsSnapshot.objects.filter(game=game, mode=mode).first()
    if snap and snap.payload:
        return snapshot_to_results_context(game, snap.payload)
    # Live path: short-TTL cached snapshot-shaped payload (shared across progressive pages).
    return snapshot_to_results_context(game, get_live_results_payload(game, mode))


def _results_me_participants(request, play_mode):
    me_personal = None
    me_anon_participant = None
    if play_mode == 'personal':
        if request.user.is_authenticated:
            me_personal = PersonalResultsParticipant(user=request.user)
        else:
            ak = _anon_key_from_request(request)
            if ak:
                me_anon_participant = PersonalResultsParticipant(anon_key=ak)
    return me_personal, me_anon_participant


def _new_results_compute(game, mode, task_group_number=None):
    team_to_list_attempts_info = {}
    team_to_score = {}
    team_to_max_best_time = {}
    team_task_to_attempts_info = {}

    placements, task_group_to_tasks, tasks_flat, task_ids, task_group_headers = (
        _load_results_placements_and_tasks(game, task_group_number=task_group_number)
    )

    # General: SQL aggregates (same as live ladder snapshot). Tournament windows and
    # alphabetty letter-hint penalty still need the ORM bulk path.
    bulk_game = results_attempts_scope_game(game, mode)
    if mode == 'general':
        from games.results_sql_aggregate import (
            get_sql_aggregated_game_actor_rows,
            tasks_need_orm_results_aggregate,
        )
        if tasks_need_orm_results_aggregate(tasks_flat):
            bulk_rows = Attempt.manager.get_bulk_game_actor_rows(
                task_ids, mode='general', game=bulk_game,
            )
        else:
            bulk_rows = get_sql_aggregated_game_actor_rows(task_ids, game=bulk_game)
    else:
        bulk_rows = Attempt.manager.get_bulk_game_actor_rows(
            task_ids, mode=mode, game=bulk_game,
        )

    for task in tasks_flat:
        for participant, attempts_info in bulk_rows.get(task.id, []):
            if mode == 'tournament' and not isinstance(participant, Team):
                continue
            if not (attempts_info.attempts or attempts_info.hint_attempts):
                continue

            if participant not in team_to_score:
                team_to_score[participant] = 0

            task_points = attempts_info.get_result_points()
            result_attempt = (
                attempts_info.get_result_attempt()
                if callable(getattr(attempts_info, 'get_result_attempt', None))
                else attempts_info.best_attempt
            )
            if task_points and task_points > 0:
                team_to_score[participant] += task_points
                if participant not in team_to_max_best_time:
                    team_to_max_best_time[participant] = result_attempt.time
                else:
                    team_to_max_best_time[participant] = max(team_to_max_best_time[participant], result_attempt.time)

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
                    hint_numbers = ai.get_hint_numbers() if callable(getattr(ai, 'get_hint_numbers', None)) else []
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
    play_mode = effective_play_mode(play_mode, game, user=request.user)
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
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
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
        'play_mode': effective_play_mode(
            _get_play_mode(request, game.project_id)[0], game, user=request.user,
        ),
        'play_mode_project_id': game.project_id,
        'page_title': 'Результаты турнира: {}'.format(game.get_no_html_name() if hasattr(game, 'get_no_html_name') else game.name),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'show_sections_nav': True,
        **_project_urls_context(game.project_id),
    })


def new_section_results_page(request, game_id):
    """
    Results table for section games. The table is shared; only ladder uses the
    compact daily layout, while other sections use the ordinary task columns.
    """
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

    progressive_page_size = 50
    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    me_personal, me_anon_participant = _results_me_participants(request, play_mode)
    results_variant = (
        'ladder' if game_id == LADDER_GAME_ID
        else 'alphabetty' if game_id == ALPHABETTY_GAME_ID
        else 'standard'
    )

    # Row data is loaded incrementally (?partial=1); initial response is headers only.
    if request.GET.get('partial') == '1':
        data = _load_game_results_data(game, mode='general')
        data = _paginate_results_rows(request, data, per_page=progressive_page_size)
        return render(request, 'new/partials/results_rows.html', {
            'mode': 'general',
            'section_results': True,
            'is_ladder_results': game_id == LADDER_GAME_ID,
            'results_variant': results_variant,
            'game': game,
            'team': team,
            'me_personal': me_personal,
            'me_anon_participant': me_anon_participant,
            **data,
        })

    snap = GameResultsSnapshot.objects.filter(game=game, mode='general').first()
    if snap and snap.payload:
        header_data = snapshot_headers_context(snap.payload)
    else:
        header_data = _results_table_headers_context(game)
    data = {**header_data, **_results_rows_empty_context()}
    data['results_column_count'] = _results_column_count(
        data.get('task_groups'), mode='general'
    )

    return render(request, 'ui/results.html', {
        'mode': 'general',
        'section_results': True,
        'is_ladder_results': game_id == LADDER_GAME_ID,
        'results_variant': results_variant,
        'game': game,
        'team': team,
        'me_personal': me_personal,
        'me_anon_participant': me_anon_participant,
        'back_url': _sections_hub_url(game.id),
        'progressive_results': True,
        'progressive_page_size': progressive_page_size,
        **data,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'page_title': 'Результаты: {}'.format(game.get_no_html_name() if hasattr(game, 'get_no_html_name') else game.name),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'show_sections_nav': True,
        **_project_urls_context(NEW_UI_PROJECT),
    })


def _render_task_group_results_page(request, game, number, back_url):
    """Shared renderer for a results table scoped to one task group."""
    team = request.user.profile.team_on if has_profile(request.user) else None
    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    me_personal, me_anon_participant = _results_me_participants(request, play_mode)
    data = _new_results_compute(game, mode='general', task_group_number=number)
    data = _paginate_results_rows(request, data, per_page=50)
    return render(request, 'ui/results.html', {
        'mode': 'general',
        'section_results': True,
        'results_variant': 'standard',
        'game': game,
        'team': team,
        'me_personal': me_personal,
        'me_anon_participant': me_anon_participant,
        'back_url': back_url,
        **data,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'page_title': 'Результаты: {} №{}'.format(game.get_no_html_name() if hasattr(game, 'get_no_html_name') else game.name, number),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'show_sections_nav': True,
        **_project_urls_context(game.project_id),
    })


def new_section_task_results_page(request, game_id, number):
    """Results for one task group of a sections-project game."""
    from games.section_paths import section_play_path

    project = Project.objects.filter(id=NEW_UI_SECTIONS_PROJECT).first()
    if not project:
        raise Http404()
    game = Game.objects.filter(project=project, id=game_id).first()
    if not game:
        raise Http404()
    if not scheduled_number_is_public(game, number):
        raise Http404()
    team = request.user.profile.team_on if has_profile(request.user) else None
    if not game.has_access('see_results', mode='general', team=team):
        raise Http404()
    placement = (
        GameTaskGroup.objects.filter(game=game, number=str(number))
        .select_related('task_group')
        .first()
    )
    if not placement:
        raise Http404()

    return _render_task_group_results_page(
        request, game, number, section_play_path(game.id, number),
    )


def new_game_task_results_page(request, game_id, number, project_id=None):
    """Results for one task group in a project-scoped game."""
    expected_project_id = project_id or NEW_UI_PROJECT
    game = get_object_or_404(Game, id=game_id, project_id=expected_project_id)
    team = _team_for_access(request)
    if not game.has_access('see_results', mode='general', team=team):
        raise Http404()
    placement = GameTaskGroup.objects.filter(game=game, number=str(number)).first()
    if not placement:
        raise Http404()
    base = _project_base(game.project_id)
    return _render_task_group_results_page(
        request, game, number,
        '{}/games/{}/{}/'.format(base, game.id, number),
    )


def _ladder_raddle_task_for_placement(placement):
    tasks = sorted(
        placement.task_group.tasks.visible().filter(~Q(task_type='text_with_forms')),
        key=lambda t: t.key_sort(),
    )
    for t in tasks:
        if t.task_type == 'raddle':
            return t
    return tasks[0] if tasks else None


def new_ladder_word_results_page(request, task_group_number):
    """
    Per-ladder standings: one column per middle word, hints 1/2 = clue/answer assists.
    URL: /ladder/<N>/results/ или /ladder/<share_hash>/results/
    """
    project = Project.objects.filter(id=NEW_UI_SECTIONS_PROJECT).first()
    if not project:
        raise Http404()
    game = Game.objects.filter(project=project, id=LADDER_GAME_ID).first()
    if not game:
        raise Http404()

    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    if not game.has_access('see_results', mode='general', team=team):
        raise Http404()

    from games.ladder_offer import get_offer_by_share_hash, is_share_hash_segment
    from types import SimpleNamespace

    ladder_offer = None
    if is_share_hash_segment(str(task_group_number)):
        ladder_offer = get_offer_by_share_hash(str(task_group_number))
        if ladder_offer is None:
            raise Http404()
        from games.ladder_offer import can_access_offer_hash
        if not can_access_offer_hash(ladder_offer, request.user):
            raise Http404()
        if ladder_offer.accepted_link_id:
            placement = (
                GameTaskGroup.objects.select_related('task_group')
                .filter(pk=ladder_offer.accepted_link_id)
                .first()
            )
            if placement is None:
                raise Http404()
        else:
            placement = SimpleNamespace(
                number=ladder_offer.share_hash,
                name=(ladder_offer.author or 'Лесенка').strip() or 'Лесенка',
                task_group=ladder_offer.task_group,
            )
    else:
        if (
            not is_ladder_number_published(game, task_group_number)
            and not request.user.is_staff
        ):
            raise Http404()
        placement = (
            GameTaskGroup.objects.select_related('task_group')
            .filter(game=game, number=str(task_group_number))
            .first()
        )
        if not placement:
            raise Http404()

    if ladder_offer is not None:
        task = (
            Task.objects.filter(task_group_id=ladder_offer.task_group_id, number='1')
            .first()
        )
        if task is None or task.task_type != 'raddle':
            task = _ladder_raddle_task_for_placement(placement) if hasattr(placement, 'task_group') else None
    else:
        task = _ladder_raddle_task_for_placement(placement)
    if not task:
        raise Http404()

    progressive_page_size = 50
    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    me_personal, me_anon_participant = _results_me_participants(request, play_mode)

    if ladder_offer is not None:
        ladder_title = placement.name or 'Лесенка'
        back_url = '/ladder/{}/'.format(ladder_offer.share_hash)
    else:
        ladder_title = placement.name or 'Лесенка №{}'.format(placement.number)
        back_url = _play_url_for_task_group(game, placement.number)

    if request.GET.get('partial') == '1':
        data = build_ladder_word_results_context(game, placement, task)
        data = _paginate_results_rows(request, data, per_page=progressive_page_size)
        return render(request, 'new/partials/results_rows.html', {
            'mode': 'general',
            'section_results': True,
            'is_ladder_results': True,
            'is_ladder_word_results': True,
            'results_variant': 'ladder_words',
            'game': game,
            'team': team,
            'me_personal': me_personal,
            'me_anon_participant': me_anon_participant,
            **data,
        })

    header_data = ladder_word_results_headers_context(task)
    data = {**header_data, **_results_rows_empty_context()}
    data['results_column_count'] = _results_column_count(
        data.get('task_groups'), mode='general'
    )

    return render(request, 'ui/results.html', {
        'mode': 'general',
        'section_results': True,
        'is_ladder_results': True,
        'is_ladder_word_results': True,
        'results_variant': 'ladder_words',
        'ladder_number': placement.number,
        'game': game,
        'team': team,
        'me_personal': me_personal,
        'me_anon_participant': me_anon_participant,
        'back_url': back_url,
        'progressive_results': True,
        'progressive_page_size': progressive_page_size,
        **data,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'page_title': 'Результаты · {}'.format(ladder_title),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'show_sections_nav': True,
        **_project_urls_context(NEW_UI_PROJECT),
    })


def _task_ui_descriptor(task, *, rld=None, rd=None, wall_meta=None, ws=None, gp=None):
    """Presentation contract for the shared new-UI task card wrapper."""
    body_templates = {
        'wall': 'task-content/task-wall.html',
        'replacements_lines': 'task-content/task-replacements-lines.html',
        'raddle': 'task-content/task-raddle.html',
        'word_salad': 'task-content/task-word-salad.html',
        'grid-puzzle': 'new/task-content/task-grid-puzzle.html',
        'proportions': 'new/task-content/task-proportions.html',
        'default': 'new/task-content/task-default.html',
        'autohint': 'new/task-content/task-default.html',
        'with_tag': 'new/task-content/task-default.html',
        'distribute_to_teams': 'new/task-content/task-default.html',
    }
    body_template = body_templates.get(task.task_type)
    body_error = ''
    if task.task_type == 'replacements_lines' and (not rld or not rld.get('n_lines')):
        body_template = None
        body_error = 'Не получилось показать это задание. Обновите страницу или напишите о проблеме.'
    elif task.task_type == 'raddle' and not rd:
        body_template = None
        body_error = 'Не получилось показать это задание. Обновите страницу или напишите о проблеме.'
    elif task.task_type == 'word_salad' and not ws:
        body_template = None
        body_error = 'Не получилось показать это задание. Обновите страницу или напишите о проблеме.'
    elif task.task_type == 'grid-puzzle' and not gp:
        body_template = None
        body_error = 'Не получилось показать это задание. Обновите страницу или напишите о проблеме.'
    if rld:
        base_max = rld['max_points_total']
    elif rd:
        base_max = rd['max_points_total']
    elif wall_meta:
        base_max = wall_meta['total']
    elif task.task_type == 'word_salad':
        base_max = task.get_results_max_points()
    else:
        base_max = task.get_points()
    attempts_hidden = {'replacements_lines', 'alphabetty', 'word_salad'}
    answer_hidden = {'replacements_lines', 'raddle', 'alphabetty', 'word_salad'}
    return {
        'body_template': body_template,
        'body_error': body_error,
        'body_wrapper': task.task_type in {'wall', 'replacements_lines', 'raddle', 'word_salad'},
        'base_max': base_max,
        'max_points_title': wall_meta.get('title', '') if wall_meta else '',
        'show_attempts': task.task_type not in attempts_hidden,
        'show_answer': task.task_type not in answer_hidden,
        'unsupported': task.task_type not in body_templates,
        'unsupported_label': 'Не получилось показать это задание. Обновите страницу или напишите о проблеме.',
    }


def build_task_group_task_context_dicts(game, task_group, tasks, team, user, anon_key, mode, placement=None):
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
    grid_puzzle_data = {}
    for t in tasks:
        if t.task_type != 'grid-puzzle':
            continue
        ai = attempts_info_by_task_id.get(t.id)
        solved = bool(ai and ai.is_solved())
        try:
            grid_puzzle_data[t.id] = public_grid_puzzle_context(
                t, reveal_solution=solved, readonly=solved,
            )
        except GridPuzzleDataError:
            continue
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
    word_salad_data = {}
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
            hint_attempts = ai.hint_attempts if ai else []
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
        elif t.task_type == 'word_salad':
            try:
                grid, words = parse_word_salad_task_data(t.checker_data, t.answer)
            except Exception:
                continue
            ai = attempts_info_by_task_id.get(t.id)
            state = load_word_salad_state(None)
            if game is not None:
                cts_qs = ChainTaskState.objects.filter(task=t, game=game)
                if team is not None:
                    cts_qs = cts_qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
                elif user is not None:
                    cts_qs = cts_qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
                elif anon_key is not None:
                    cts_qs = cts_qs.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
                else:
                    cts_qs = cts_qs.none()
                play_mode_key = 'tournament' if mode == 'tournament' else 'general'
                cts = cts_qs.filter(game_mode=play_mode_key).first()
                if cts and cts.state:
                    state = load_word_salad_state(cts.state)
                elif ai and ai.attempts:
                    for a in reversed(ai.attempts):
                        if a.state:
                            state = load_word_salad_state(a.state)
                            break
            elif ai and ai.attempts:
                for a in reversed(ai.attempts):
                    if a.state:
                        state = load_word_salad_state(a.state)
                        break
            word_salad_data[t.id] = build_word_salad_ui_context(
                grid, words, state, attempts=ai.attempts if ai else [],
            )
    raddle_data = {}
    for t in tasks:
        if t.task_type == 'raddle':
            parsed = parse_raddle_data(t)
            if not parsed:
                continue
            ai = attempts_info_by_task_id.get(t.id)
            raddle_hint_attempts = ai.hint_attempts if ai else []
            state = load_raddle_state(None, parsed['n_words'])
            # Предпочитаем ChainTaskState (источник правды для чекера); иначе Attempt.state.
            if game is not None:
                cts_qs = ChainTaskState.objects.filter(task=t, game=game)
                if team is not None:
                    cts_qs = cts_qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
                elif user is not None:
                    cts_qs = cts_qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
                elif anon_key is not None:
                    cts_qs = cts_qs.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
                else:
                    cts_qs = cts_qs.none()
                play_mode_key = 'tournament' if mode == 'tournament' else 'general'
                cts = cts_qs.filter(game_mode=play_mode_key).first()
                if cts and cts.state:
                    state = load_raddle_state(cts.state, parsed['n_words'])
                elif ai and ai.attempts:
                    for a in reversed(ai.attempts):
                        if a.state:
                            state = load_raddle_state(a.state, parsed['n_words'])
                            break
            elif ai and ai.attempts:
                for a in reversed(ai.attempts):
                    if a.state:
                        state = load_raddle_state(a.state, parsed['n_words'])
                        break
            ui = build_raddle_ui_context(
                parsed, state, ai.attempts if ai else [],
                max_attempts=t.get_max_attempts(), mode=mode,
                hint_attempts=raddle_hint_attempts,
            )
            raddle_data[t.id] = {
                'parsed': parsed,
                'ui': ui,
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
    if placement is None and getattr(task_group, 'pk', None) and getattr(game, 'pk', None):
        placement = (
            GameTaskGroup.objects
            .filter(game_id=game.pk, task_group_id=task_group.pk)
            .only('number', 'name')
            .first()
        )
    task_ui_by_task_id = {
        t.id: {
            **_task_ui_descriptor(
                t,
                rld=replacements_lines_data.get(t.id),
                rd=raddle_data.get(t.id),
                wall_meta=wall_max_points_meta_by_task_id.get(t.id),
                ws=word_salad_data.get(t.id),
                gp=grid_puzzle_data.get(t.id),
            ),
            'display_name': task_display_name(game, t, placement=placement),
        }
        for t in tasks
    }
    return {
        'attempts_info_by_task_id': attempts_info_by_task_id,
        'wall_max_points_meta_by_task_id': wall_max_points_meta_by_task_id,
        'likes_meta_by_task_id': likes_meta_by_task_id,
        'replacements_lines_data': replacements_lines_data,
        'word_salad_data': word_salad_data,
        'grid_puzzle_data': grid_puzzle_data,
        'raddle_data': raddle_data,
        'proportions_chips': proportions_chips,
        'task_ui_by_task_id': task_ui_by_task_id,
    }


def new_task_group_page(request, game_id, task_group_number):
    game = get_object_or_404(
        Game.objects.select_related('section_default_rules'),
        id=game_id,
    )

    # Обычные игры: до старта / без регистрации — карточка анонса, не 404.
    if game.project_id != NEW_UI_SECTIONS_PROJECT:
        gate = _maybe_registration_or_announce_response(
            request,
            game,
            back_url='/games/',
            show_sections_nav=True,
        )
        if gate is not None:
            return gate

    play_mode, play_mode_key = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    anon_key = None

    if not request.user.is_authenticated:
        if personal_play_mode_locked(game, user=request.user):
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
    ladder_offer = None
    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        preview_team = None
        if has_profile(request.user):
            preview_team = request.user.profile.team_on
        if not game.has_access('see_game_preview', team=preview_team):
            raise Http404()
        if game_id == LADDER_GAME_ID:
            from games.ladder_offer import get_offer_by_share_hash, is_share_hash_segment
            if is_share_hash_segment(str(task_group_number)):
                ladder_offer = get_offer_by_share_hash(str(task_group_number))
                if ladder_offer is None:
                    raise Http404()
                from games.ladder_offer import can_access_offer_hash
                if not can_access_offer_hash(ladder_offer, request.user):
                    raise Http404()
            elif (
                not scheduled_number_is_public(game, task_group_number)
                and not request.user.is_staff
            ):
                raise Http404()
        elif not scheduled_number_is_public(game, task_group_number):
            raise Http404()
    else:
        if play_mode == 'team':
            if not game.has_access('play', team=team):
                raise Http404()
        else:
            if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
                raise Http404()
            if not game.is_playable:
                raise Http404()

    mode = game.get_current_mode(Attempt(time=timezone.now()))
    if ladder_offer is not None:
        from types import SimpleNamespace
        if ladder_offer.accepted_link_id:
            placement = (
                GameTaskGroup.objects.select_related('task_group', 'task_group__rules')
                .filter(pk=ladder_offer.accepted_link_id)
                .first()
            )
            if placement is None:
                raise Http404()
        else:
            placement = SimpleNamespace(
                pk=None,
                number=ladder_offer.share_hash,
                name=(ladder_offer.author or 'Лесенка').strip() or 'Лесенка',
                task_group=ladder_offer.task_group,
            )
    else:
        placement = (
            GameTaskGroup.objects.select_related('task_group', 'task_group__rules')
            .filter(game=game, number=str(task_group_number))
            .first()
        )
        if not placement:
            fallback = GameTaskGroup.nearest_by_number(game, task_group_number)
            if fallback:
                return redirect(_play_url_for_task_group(game, fallback.number))
            raise Http404()
    task_group = placement.task_group
    if game.id == LADDER_GAME_ID and ladder_offer is not None:
        prev_tg, next_tg = None, None
    elif is_scheduled_game(game.id):
        published_nav = list(visible_links(_game_task_group_links(game), game))
        prev_tg, next_tg = _neighbors_by_pk(published_nav, placement)
    else:
        prev_tg, next_tg = GameTaskGroup.prev_next_for(game, placement)
    tasks = sorted(task_group.tasks.visible(), key=lambda t: t.key_sort())
    section_rules_type = game.id if game.id in SECTION_RULES_GAME_IDS else None
    section_tutorial_html = _section_tutorial_html_for_game(game)
    show_palindrome_rules = game.id == PALINDROMES_GAME_ID
    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        tg_rules = placement.task_group.rules
        if tg_rules and (tg_rules.html or '').strip():
            section_rules_type = None
            section_tutorial_html = None
            show_palindrome_rules = False
    ctx_dicts = build_task_group_task_context_dicts(
        game, task_group, tasks, team, user, anon_key, mode,
        placement=placement if isinstance(placement, GameTaskGroup) else None,
    )
    week_task_source_line = None
    week_task_source_url = None
    if game.id == WEEK_TASK_GAME_ID:
        tags = task_group.tags or {}
        src = source_summary_from_tags(tags)
        des = src.get('desyatka_label') or src.get('game_id')
        if des:
            week_task_source_line = 'из {}'.format(des)
            week_task_source_url = source_play_path_from_tags(tags) or None
    source_desyatka = None
    if game.id in ('replacements', 'walls', 'palindromes'):
        source_desyatka = get_source_desyatka_context(task_group, team=team)
    from games.section_paths import ladder_word_results_path
    is_daily_single_task = uses_daily_play_layout(game.id)
    daily_publish_date = None
    if ladder_offer is None:
        pub_at = publish_at_for(game, placement.number)
        if pub_at is not None:
            daily_publish_date = pub_at.date()
    elif ladder_offer.accepted_link_id:
        try:
            prod_n = int(ladder_offer.accepted_link.number)
        except (TypeError, ValueError, AttributeError):
            prod_n = None
        if prod_n is not None:
            pub_at = publish_at_for(game, prod_n)
            if pub_at is not None:
                daily_publish_date = pub_at.date()
    if ladder_offer is not None:
        if ladder_offer.accepted_link_id and str(getattr(ladder_offer.accepted_link, 'number', '')).isdigit():
            page_title = '{} №{}'.format(
                game.outside_name or game.name,
                ladder_offer.accepted_link.number,
            )
        else:
            page_title = '{} · {}'.format(
                game.outside_name or game.name,
                placement.name or 'предложение',
            )
    elif is_daily_single_task:
        page_title = task_group_page_title(game, placement)
    else:
        page_title = task_group_page_title(game, placement)
    if ladder_offer is not None and not isinstance(placement, GameTaskGroup):
        for ui in ctx_dicts['task_ui_by_task_id'].values():
            ui['display_name'] = page_title

    can_reset_offer = False
    offer_reset_url = None
    if ladder_offer is not None and request.user.is_authenticated:
        if ladder_offer.user_id == request.user.id or request.user.is_staff:
            can_reset_offer = True
            offer_reset_url = '/create_ladder/{}/reset/'.format(ladder_offer.pk)

    if ladder_offer is not None:
        ladder_results_url = ladder_word_results_path(ladder_offer.share_hash)
    elif game.id == LADDER_GAME_ID:
        ladder_results_url = ladder_word_results_path(placement.number)
    else:
        ladder_results_url = None

    daily_footer_enabled = is_daily_single_task
    daily_game_label = section_nav_title(game.id) or None
    daily_results_allowed = bool(
        ladder_results_url
        and game.has_access('see_results', mode='general', team=team)
    )

    back_url = (
        _sections_hub_url(game.id)
        if game.project_id == NEW_UI_SECTIONS_PROJECT
        else (
            '/games/{}/'.format(game.id)
            if game.project_id == NEW_UI_PROJECT
            else '/'
        )
    )
    if ladder_offer is not None and request.user.is_authenticated and ladder_offer.user_id == request.user.id:
        back_url = '/create_ladder/'

    return render(request, 'ui/task_group.html', {
        'game': game,
        'task_group': task_group,
        'tasks': tasks,
        'attempts_info_by_task_id': ctx_dicts['attempts_info_by_task_id'],
        'replacements_lines_data': ctx_dicts['replacements_lines_data'],
        'word_salad_data': ctx_dicts['word_salad_data'],
        'raddle_data': ctx_dicts['raddle_data'],
        'proportions_chips': ctx_dicts['proportions_chips'],
        'wall_max_points_meta_by_task_id': ctx_dicts['wall_max_points_meta_by_task_id'],
        'likes_meta_by_task_id': ctx_dicts['likes_meta_by_task_id'],
        'task_ui_by_task_id': ctx_dicts['task_ui_by_task_id'],
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
        'prev_task_group_url': (
            _play_url_for_task_group(game, prev_tg.number) if prev_tg else None
        ),
        'next_task_group_url': (
            _play_url_for_task_group(game, next_tg.number) if next_tg else None
        ),
        'task_group_results_url': _task_group_results_url(game, placement.number),
        'task_group_results_allowed': game.has_access('see_results', mode='general', team=team),
        'tg_number': placement.number,
        'tg_name': placement.name,
        'share_host': share_host_from_request(request),
        'week_task_source_line': week_task_source_line,
        'week_task_source_url': week_task_source_url,
        'source_desyatka': source_desyatka,
        'is_daily_single_task': is_daily_single_task,
        'daily_publish_date': daily_publish_date,
        'ladder_word_results_url': ladder_results_url,
        'daily_footer_enabled': daily_footer_enabled,
        'daily_game_label': daily_game_label,
        'daily_results_url': ladder_results_url,
        'daily_results_allowed': daily_results_allowed,
        'daily_results_label': 'Результаты' if game.id == LADDER_GAME_ID else '',
        **section_format_credit_context(game.id),
        'daily_pager_aria_label': 'Переход между {}'.format(
            'лесенками' if game.id == LADDER_GAME_ID
            else 'заданиями недели' if game.id == WEEK_TASK_GAME_ID
            else 'кругами'
        ),
        'ladder_offer': ladder_offer,
        'can_reset_ladder_offer': can_reset_offer,
        'ladder_offer_reset_url': offer_reset_url,
        'back_url': back_url,
        **_task_group_page_nav_context(game, prev_tg=prev_tg, next_tg=next_tg),
        'page_title': page_title,
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'show_sections_nav': True,
        'live_next_transition_at': (
            None if ladder_offer is not None
            else next_daily_content_transition_for_game(game)
        ),
        **_project_urls_context(game.project_id),
        **_age_gate_context(
            game,
            task_group=task_group,
            back_url=back_url,
        ),
    })


@never_cache
@require_http_methods(['GET'])
def new_task_group_live_state(request, game_id):
    """Authoritative task-card projection used after socket gaps and reconnects."""
    game = get_object_or_404(Game, id=game_id)
    play_mode, _play_mode_key = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    team = user = anon_key = None

    if request.user.is_authenticated:
        if play_mode == 'team':
            if not has_team(request.user):
                raise Http404()
            team = request.user.profile.team_on
        else:
            if not has_profile(request.user):
                raise Http404()
            user = request.user
    else:
        if personal_play_mode_locked(game, user=request.user):
            raise Http404()
        play_mode = 'personal'
        anon_key = _anon_key_from_request(request)
        if not anon_key:
            raise Http404()

    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        preview_team = request.user.profile.team_on if has_profile(request.user) else None
        if not game.has_access('see_game_preview', team=preview_team):
            raise Http404()
    elif play_mode == 'team':
        if not game.has_access('play', team=team):
            raise Http404()
    else:
        now_attempt = Attempt(time=timezone.now())
        if (
            not game.has_access('read_googledoc', team=None, attempt=now_attempt)
            or not game.is_playable
        ):
            raise Http404()

    raw_ids = (request.GET.get('task_ids') or '').strip()
    try:
        task_ids = list(dict.fromkeys(int(value) for value in raw_ids.split(',') if value.strip()))
    except (TypeError, ValueError):
        return JsonResponse({'status': 'invalid_task_ids'}, status=400)
    if not task_ids or len(task_ids) > 100:
        return JsonResponse({'status': 'invalid_task_ids'}, status=400)

    tasks = list(
        Task.objects.visible()
        .select_related('task_group')
        .filter(pk__in=task_ids)
    )
    if len(tasks) != len(task_ids):
        raise Http404()
    tasks_by_id = {task.pk: task for task in tasks}
    tasks = [tasks_by_id[task_id] for task_id in task_ids]

    placements = list(
        GameTaskGroup.objects.filter(
            game=game,
            task_group_id__in={task.task_group_id for task in tasks},
        ).select_related('task_group')
    )
    placements_by_group = {placement.task_group_id: placement for placement in placements}
    if any(task.task_group_id not in placements_by_group for task in tasks):
        raise Http404()

    if not request.user.is_staff:
        for placement in placements:
            if not scheduled_number_is_public(game, placement.number):
                raise Http404()

    from games.views.render_task import render_new_ui_task_card_html
    from games.views.track import current_track_versions

    # Sample revisions before rendering. A mutation racing with this request may
    # then produce a newer queued socket event, but an older projection can never
    # claim that newer revision and suppress the event on the client.
    versions = current_track_versions(
        game.id,
        user_id=request.user.id if request.user.is_authenticated else None,
        team_id=team.pk if team is not None else None,
    )
    mode = game.get_current_mode(Attempt(time=timezone.now()))
    fragments = {}
    for task in tasks:
        fragment = render_new_ui_task_card_html(
            request,
            task,
            team,
            mode,
            user=user,
            anon_key=anon_key,
            game=game,
        )
        if fragment:
            fragments[str(task.pk)] = fragment

    return JsonResponse({
        'status': 'ok',
        'update_task_html_new': fragments,
        'versions': versions,
        'reload_required': len(fragments) != len(tasks),
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
    play_mode = effective_play_mode(play_mode, game, user=request.user)
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

    if task.task_type == 'raddle':
        return JsonResponse({
            'html': (
                '<div class="new-login-hint">Для raddle ответ показывается у каждого '
                'решённого слова (кнопка «Ответ»).</div>'
            ),
        })

    if task.task_type == 'grid-puzzle':
        try:
            gp = public_grid_puzzle_context(task, reveal_solution=True, readonly=True)
        except GridPuzzleDataError:
            return JsonResponse({'html': '<div class="new-login-hint">Не удалось показать ответ.</div>'})
        return JsonResponse({
            'html': render_to_string(
                'new/task-content/grid-puzzle-answer.html',
                {'task': task, 'gp': gp},
                request=request,
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
    play_mode = effective_play_mode(play_mode, game, user=request.user)
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


@require_http_methods(['GET'])
def new_get_raddle_word_answer(request, task_id, word_index):
    task = get_public_task_or_404(task_id)
    if task.task_type != 'raddle':
        raise Http404()
    game = game_from_request_for_task(request, task)
    if game is None:
        raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
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
        word_index_int = int(word_index)
    except (TypeError, ValueError):
        word_index_int = -1
    word_done_list = []
    parsed = parse_raddle_data(task)
    if parsed:
        word_done_list = raddle_word_solved_list(
            parsed, attempts_info.attempts if attempts_info else [],
        )
    if mode != 'general':
        if not attempts_info.is_solved():
            if (
                word_index_int < 0
                or word_index_int >= len(word_done_list)
                or not word_done_list[word_index_int]
            ):
                return JsonResponse({'html': '<div class="new-login-hint">Ответ доступен после верного решения.</div>'})

    parsed = parse_raddle_data(task)
    if not parsed or word_index_int < 0 or word_index_int >= parsed['n_words']:
        return JsonResponse({'html': '<div class="new-login-hint">Нет ответа.</div>'})
    text = parsed['words'][word_index_int]
    if not text.strip():
        return JsonResponse({'html': '<div class="new-login-hint">Нет ответа.</div>'})
    return JsonResponse({'html': _answer_popup_html(text, task.answer_comment)})


@require_http_methods(['POST'])
def new_like_dislike(request, task_id):
    game_hint = (
        (request.POST.get('game_id') or request.GET.get('game_id') or '').strip()
        or (request.headers.get('X-Interoves-Game') or '').strip()
    )
    try:
        task = get_public_task_or_404(task_id)
    except Http404:
        task = Task.objects.filter(pk=task_id).select_related('task_group').first()
        if task is None:
            raise
        if game_hint == ALPHABETTY_GAME_ID:
            from games.models import AlphabettyOffer
            if not AlphabettyOffer.objects.filter(task_group_id=task.task_group_id).exists():
                raise Http404()
        elif game_hint == LADDER_GAME_ID:
            from games.models import LadderOffer
            if not LadderOffer.objects.filter(task_group_id=task.task_group_id).exists():
                raise Http404()
        else:
            raise Http404()
    game = game_from_request_for_task(request, task)
    if game is None:
        if game_hint in (ALPHABETTY_GAME_ID, LADDER_GAME_ID):
            game = Game.objects.filter(id=game_hint).first()
        if game is None:
            raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
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
        reaction = 1
    elif dislikes == 1:
        reaction = -1
    elif likes == -1 or dislikes == -1:
        reaction = 0
    else:
        reaction = None
    if reaction is not None:
        Like.manager.set_actor_reaction(
            task, reaction, team=team, user=user, anon_key=anon_key,
        )

    return JsonResponse({
        # показываем сумму КОМАНДНЫХ + ЛИЧНЫХ лайков/дизлайков
        'likes': Like.manager.get_total_likes(task),
        'dislikes': Like.manager.get_total_dislikes(task),
        # а состояние — текущего режима
        'liked': Like.manager.actor_has_like(task, team=team, user=user, anon_key=anon_key),
        'disliked': Like.manager.actor_has_dislike(task, team=team, user=user, anon_key=anon_key),
    })


@require_http_methods(['POST'])
def new_bug_report(request, task_id):
    task = get_public_task_or_404(task_id)
    game = game_from_request_for_task(request, task)
    if game is None:
        raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    team = None
    user = request.user if request.user.is_authenticated else None
    anon_key = None
    if play_mode == 'team':
        if not has_profile(request.user) or not request.user.profile.team_on:
            raise Http404()
        team = request.user.profile.team_on
        if not game.has_access('send_attempt', team=team):
            raise Http404()
    else:
        if user is None:
            anon_key = request.POST.get('anon_key')
            if not anon_key:
                raise Http404()
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise Http404()

    text = (request.POST.get('text') or '').strip()
    if not text:
        return JsonResponse({'ok': False, 'error': 'Опишите проблему.'}, status=400)
    if len(text) > 5000:
        return JsonResponse({'ok': False, 'error': 'Слишком длинное сообщение.'}, status=400)

    from games.feedback import profile_reports_path
    from games.telegram.game_urls import task_play_url

    report = BugReport.objects.create(
        task=task,
        game=game,
        team=team,
        user=user,
        anon_key=anon_key,
        text=text,
        page_url=task_play_url(game, task),
        status='Pending',
    )
    return JsonResponse({
        'ok': True,
        'report_id': report.pk,
        'report_url': profile_reports_path(project_id=game.project_id, report_id=report.pk),
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
        if g is not None and personal_play_mode_locked(g, user=request.user):
            mode = 'team'
    if mode in ('team', 'personal'):
        request.session[_session_play_mode_key(project_id)] = mode
    return redirect(next_url)


def _valid_anon_key(value):
    value = (value or '').strip()
    return bool(
        8 <= len(value) <= 64
        and all(char.isalnum() or char in '-_.~' for char in value)
    )


def _anon_key_matches_browser(request, anon_key):
    """A cookie, when available, is stronger proof than a posted bearer key."""
    cookie_key = (request.COOKIES.get('interoves_anon') or '').strip()
    return not cookie_key or hmac.compare_digest(cookie_key, anon_key)


def _anon_task_group_link(attempt):
    """Ссылка (url, label) на круг с этим заданием, либо None, если её не построить.

    У нас нет отдельных страниц заданий — ведём на страницу круга (task group).
    """
    task = attempt.task
    if task is None or task.task_group_id is None:
        return None
    game = attempt.game or GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        return None
    gtg = GameTaskGroup.objects.filter(
        game=game, task_group_id=task.task_group_id
    ).first()
    if gtg is None:
        return None
    try:
        url = reverse('new_task_group', kwargs={
            'game_id': game.id,
            'task_group_number': gtg.number,
        })
    except Exception:
        return None
    label = (gtg.name or '').strip() or (game.name or '').strip()
    return url, label


@login_required
@require_http_methods(['GET'])
def new_anon_migrate_count(request):
    if not has_profile(request.user):
        raise Http404()
    anon_key = (request.GET.get('anon_key') or '').strip()
    if not anon_key:
        return JsonResponse({'attempts': 0, 'show_prompt': False})
    if not _valid_anon_key(anon_key):
        return JsonResponse({'status': 'invalid_anon_key', 'show_prompt': False}, status=400)
    if not _anon_key_matches_browser(request, anon_key):
        return JsonResponse({'status': 'anon_key_mismatch', 'show_prompt': False}, status=403)

    from games.anon_migrate import anon_migration_counts
    from games.models import AnonAccountClaim, HiddenAnonKey

    if HiddenAnonKey.objects.filter(anon_key=anon_key).exists():
        return JsonResponse({'status': 'hidden_anon', 'show_prompt': False}, status=409)
    claim = AnonAccountClaim.objects.filter(anon_key=anon_key).first()
    if claim is not None and claim.user_id != request.user.pk:
        return JsonResponse({'status': 'claimed_elsewhere', 'show_prompt': False}, status=409)

    # Задания, уже сданные на OK на авторизованном профиле (личный режим),
    # не учитываем — их посылки восстанавливать незачем.
    solved_task_ids = set(
        Attempt.manager.filter(
            user=request.user, team__isnull=True, anon_key__isnull=True, status='Ok',
        ).values_list('task_id', flat=True)
    )

    anon_attempts = (
        Attempt.manager.filter(
            anon_key=anon_key, user__isnull=True, team__isnull=True, task__isnull=False,
        )
        .select_related('task', 'game')
        .order_by('time')
    )

    n = 0
    example = None
    for attempt in anon_attempts:
        if attempt.task_id in solved_task_ids:
            continue
        n += 1
        if example is None:
            example = _anon_task_group_link(attempt)

    counts = anon_migration_counts(anon_key)
    counts['unsolved_attempts'] = n
    counts['transferable_hints'] = HintAttempt.objects.filter(
        anon_key=anon_key, user__isnull=True, team__isnull=True,
    ).exclude(hint__task_id__in=solved_task_ids).count()
    counts['transferable_states'] = ChainTaskState.objects.filter(
        anon_key=anon_key, user__isnull=True, team__isnull=True,
    ).exclude(task_id__in=solved_task_ids).count()
    total_items = (
        n
        + sum(
            value for key, value in counts.items()
            if key not in (
                'attempts', 'unsolved_attempts', 'hints', 'states',
                'analytics_states', 'transferable_hints', 'transferable_states',
            )
        )
        + counts['transferable_hints']
        + counts['transferable_states']
    )
    show_prompt = total_items > 0
    payload = {
        'status': 'ok',
        # Backward-compatible meaning: attempts that can add progress to this
        # user. The full raw count is available as counts.attempts.
        'attempts': n,
        'counts': counts,
        'total_items': total_items,
        'show_prompt': show_prompt,
    }
    if example is not None:
        payload['example_url'] = example[0]
        payload['example_label'] = example[1]
    return JsonResponse(payload)


@login_required
@require_http_methods(['POST'])
@transaction.atomic
def new_migrate_anon_attempts(request):
    if not has_profile(request.user):
        raise Http404()
    anon_key = (request.POST.get('anon_key') or '').strip()
    if not anon_key:
        raise Http404()
    if not _valid_anon_key(anon_key):
        return JsonResponse({'status': 'invalid_anon_key'}, status=400)
    if not _anon_key_matches_browser(request, anon_key):
        return JsonResponse({'status': 'anon_key_mismatch'}, status=403)

    from games.anon_migrate import anon_migration_counts
    from games.models import AnonAccountClaim, HiddenAnonKey

    if HiddenAnonKey.objects.select_for_update().filter(anon_key=anon_key).exists():
        return JsonResponse({'status': 'hidden_anon'}, status=409)
    claim = AnonAccountClaim.objects.select_for_update().filter(anon_key=anon_key).first()
    if claim is not None and claim.user_id != request.user.pk:
        return JsonResponse({'status': 'claimed_elsewhere'}, status=409)
    before_counts = anon_migration_counts(anon_key)
    if claim is None and any(before_counts.values()):
        # get_or_create resolves the unique-key race if two authenticated
        # sessions try to claim the same browser identity simultaneously.
        claim, _ = AnonAccountClaim.objects.get_or_create(
            anon_key=anon_key,
            defaults={'user': request.user},
        )
        if claim.user_id != request.user.pk:
            return JsonResponse({'status': 'claimed_elsewhere'}, status=409)
    moved = Attempt.manager.filter(anon_key=anon_key, user__isnull=True, team__isnull=True).update(
        user=request.user,
        anon_key=None,
    )
    moved_hints = HintAttempt.objects.filter(anon_key=anon_key, user__isnull=True, team__isnull=True).update(
        user=request.user,
        anon_key=None,
    )
    from games.anon_migrate import (
        migrate_anon_analytics_state,
        migrate_anon_attributions,
        migrate_anon_chain_task_states,
        migrate_anon_completed_games,
        migrate_anon_likes,
        migrate_anon_personal_dict_words,
        migrate_anon_started_games,
    )
    moved_states = migrate_anon_chain_task_states(request.user, anon_key)
    moved_starts = migrate_anon_started_games(request.user, anon_key)
    moved_completions = migrate_anon_completed_games(request.user, anon_key)
    moved_analytics_state = migrate_anon_analytics_state(request.user, anon_key)
    moved_personal_dict = migrate_anon_personal_dict_words(request.user, anon_key)
    moved_likes = migrate_anon_likes(request.user, anon_key)
    moved_attributions = migrate_anon_attributions(request.user, anon_key)
    moved_bug_reports = moved_attributions['bug_reports']
    moved_dict_suggestions = moved_attributions['dict_suggestions']
    if (
        moved or moved_hints or moved_states or moved_starts or moved_completions
        or moved_analytics_state or moved_personal_dict or moved_likes
        or moved_bug_reports or moved_dict_suggestions
    ):
        StatisticsEvent.record(
            StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED,
            user=request.user,
            anon_key=anon_key,
            moved=moved,
            moved_hints=moved_hints,
            moved_states=moved_states,
            moved_starts=moved_starts,
            moved_completions=moved_completions,
            moved_analytics_state=moved_analytics_state,
            moved_personal_dict=moved_personal_dict,
            moved_likes=moved_likes,
            moved_bug_reports=moved_bug_reports,
            moved_dict_suggestions=moved_dict_suggestions,
        )
    return JsonResponse({
        'status': 'ok',
        'moved': moved,
        'moved_hints': moved_hints,
        'moved_states': moved_states,
        'moved_starts': moved_starts,
        'moved_completions': moved_completions,
        'moved_analytics_state': moved_analytics_state,
        'moved_personal_dict': moved_personal_dict,
        'moved_likes': moved_likes,
        'moved_bug_reports': moved_bug_reports,
        'moved_dict_suggestions': moved_dict_suggestions,
    })


class ProfileSettingsForm(ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].widget.attrs.update({'placeholder': 'Имя'})
        self.fields['last_name'].widget.attrs.update({'placeholder': 'Фамилия'})
        self.fields['telegram_handle'].required = False
        self.fields['telegram_handle'].widget.attrs.update({
            'placeholder': 'username без @',
            'autocomplete': 'off',
        })
        if getattr(self.instance, 'telegram_verified', False):
            verified_handle = (
                getattr(self.instance, 'telegram_username', '')
                or getattr(self.instance, 'telegram_handle', '')
            )
            if verified_handle:
                self.initial['telegram_handle'] = verified_handle.lstrip('@')
                self.fields['telegram_handle'].disabled = True
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
        fields = ['first_name', 'last_name', 'telegram_handle', 'avatar_url', 'timezone']
        widgets = {
            'first_name': TextInput(),
            'last_name': TextInput(),
            'telegram_handle': TextInput(),
            'avatar_url': TextInput(),
        }

    def clean_telegram_handle(self):
        from games.ladder_offer import normalize_telegram_handle
        return normalize_telegram_handle(self.cleaned_data.get('telegram_handle') or '')

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


def _profile_unavailable_redirect(request):
    messages.error(request, 'Профиль недоступен.')
    scoped = _scoped_project_id(request)
    if scoped:
        return redirect('project_hub', project_id=scoped)
    return redirect('new_hub')


@login_required
@require_http_methods(['GET'])
def new_profile_reports(request, project_id=None):
    if not has_profile(request.user):
        return _profile_unavailable_redirect(request)
    from games.feedback import preview_text, reports_for_user, unread_report_ids_for_user

    reports = list(reports_for_user(request.user))
    unread_ids = unread_report_ids_for_user(request.user)
    cards = []
    for report in reports:
        cards.append({
            'report': report,
            'preview': preview_text(report.text),
            'status_label': report.status_label_ru(),
            'unread': report.pk in unread_ids,
            'task_url': report.page_url or '',
        })
    scoped = _scoped_project_id(request) or project_id
    ctx = {
        'report_cards': cards,
        'page_title': 'Мои обращения',
    }
    ctx.update(_project_urls_context(scoped or NEW_UI_PROJECT))
    _merge_nav_project_for_scope(ctx, request, scoped)
    return render(request, 'ui/profile_reports.html', ctx)


@login_required
@require_http_methods(['GET', 'POST'])
def new_profile_report_detail(request, report_id, project_id=None):
    if not has_profile(request.user):
        return _profile_unavailable_redirect(request)
    from games.feedback import add_user_reply, mark_report_read

    report = get_object_or_404(
        BugReport.objects.select_related('task', 'task__task_group', 'game', 'game__project', 'user'),
        pk=report_id,
        user=request.user,
    )
    if request.method == 'POST':
        text = (request.POST.get('text') or '').strip()
        if not text:
            messages.error(request, 'Напишите сообщение.')
        elif not report.user_can_reply():
            messages.error(request, 'Это обращение закрыто.')
        else:
            try:
                add_user_reply(report, request.user, text)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, 'Сообщение отправлено.')
                return _report_detail_redirect(request, report.pk)
    mark_report_read(report)
    scoped = _scoped_project_id(request) or project_id
    ctx = {
        'report': report,
        'thread': list(report.messages.select_related('author_user').all()),
        'can_reply': report.user_can_reply(),
        'status_label': report.status_label_ru(),
        'task_url': report.page_url or '',
        'page_title': 'Обращение',
    }
    ctx.update(_project_urls_context(scoped or NEW_UI_PROJECT))
    _merge_nav_project_for_scope(ctx, request, scoped)
    return render(request, 'ui/profile_report_detail.html', ctx)


@login_required
@require_http_methods(['GET', 'POST'])
def new_profile(request, project_id=None):
    if not has_profile(request.user):
        return _profile_unavailable_redirect(request)
    profile = request.user.profile
    connected_accounts = list(
        SocialAccount.objects.filter(user=request.user).order_by('provider', 'id')
    )
    connected = {account.provider for account in connected_accounts}
    connected_account_labels = {}
    connected_account_ids = {}
    for account in connected_accounts:
        extra = account.extra_data or {}
        label = (
            extra.get('email')
            or extra.get('name')
            or extra.get('preferred_username')
            or extra.get('screen_name')
            or str(account.uid)
        )
        connected_account_labels.setdefault(account.provider, label)
        connected_account_ids.setdefault(account.provider, account.pk)
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
    from games.feedback import profile_cabinet_flags
    ctx = {
        'form': form,
        'connected_providers': connected,
        'connected_accounts': connected_accounts,
        'connected_account_labels': connected_account_labels,
        'connected_account_ids': connected_account_ids,
        'can_disconnect_social_account': len(connected_accounts) > 1,
        'tz_options': tz_options,
        'page_title': 'Профиль',
    }
    ctx.update(profile_cabinet_flags(request.user))
    ctx.update(_project_urls_context(scoped or NEW_UI_PROJECT))
    _merge_nav_project_for_scope(ctx, request, scoped)
    return render(request, 'ui/profile.html', ctx)


@login_required
@require_http_methods(['GET', 'POST'])
def new_account_merge_confirm(request):
    from django.contrib.auth import get_user_model
    from games.account_merge import (
        AccountMergeError,
        build_account_merge_preview,
        clear_pending_account_merge,
        get_pending_account_merge,
        merge_accounts,
    )

    pending = get_pending_account_merge(request)
    if pending is None:
        messages.info(request, 'Запрос на объединение аккаунтов истёк или уже был использован.')
        return redirect('ui_profile')

    source = get_user_model().objects.filter(pk=pending['source_user_id']).first()
    if source is None:
        clear_pending_account_merge(request)
        messages.error(request, 'Второй профиль больше не найден.')
        return redirect('ui_profile')

    preview = build_account_merge_preview(request.user, source)
    provider_label = {'google': 'Google', 'vk': 'VK', 'telegram': 'Telegram'}.get(
        pending['provider'], pending['provider'],
    )
    if request.method == 'POST':
        if request.POST.get('action') != 'merge':
            next_url = pending['next']
            clear_pending_account_merge(request)
            messages.info(request, 'Аккаунты оставлены раздельными.')
            return redirect(next_url)
        if not hmac.compare_digest(
            request.POST.get('nonce') or '', pending['nonce'],
        ):
            clear_pending_account_merge(request)
            messages.error(request, 'Запрос устарел. Подключите аккаунт ещё раз.')
            return redirect('ui_profile')
        try:
            merge = merge_accounts(
                target_user=request.user,
                source_user=source,
                provider=pending['provider'],
                provider_uid=pending['provider_uid'],
            )
        except AccountMergeError as exc:
            messages.error(request, str(exc))
        else:
            next_url = pending['next']
            clear_pending_account_merge(request)
            messages.success(
                request,
                'Профили объединены. Можно входить через {}.'.format(
                    provider_label,
                ),
            )
            return redirect(next_url)

    return render(request, 'ui/account_merge_confirm.html', {
        'preview': preview,
        'pending_merge': pending,
        'provider_label': provider_label,
        'page_title': 'Объединение аккаунтов',
    })


@login_required
@require_http_methods(['POST'])
def new_social_account_disconnect(request):
    account = SocialAccount.objects.filter(
        pk=request.POST.get('account_id'), user=request.user,
    ).first()
    if account is None:
        raise Http404()
    if not SocialAccount.objects.filter(user=request.user).exclude(pk=account.pk).exists():
        messages.error(request, 'Нельзя отключить единственный способ входа.')
        return _profile_redirect(request)

    from allauth.socialaccount import signals as socialaccount_signals
    from allauth.socialaccount.internal.flows.connect import validate_disconnect
    from django.utils.http import url_has_allowed_host_and_scheme

    provider_label = {'google': 'Google', 'vk': 'VK', 'telegram': 'Telegram'}.get(
        account.provider, account.provider,
    )
    validate_disconnect(request, account)
    account.delete()
    socialaccount_signals.social_account_removed.send(
        sender=SocialAccount, request=request, socialaccount=account,
    )
    messages.success(request, '{} отключён. Остальные способы входа продолжают работать.'.format(
        provider_label,
    ))
    next_url = request.POST.get('next') or ''
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return _profile_redirect(request)


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


def _team_ticket_requests_page(request, team, *, page_param='tr_page', per_page=TICKET_REQUESTS_PAGE_SIZE):
    empty = {
        'ticket_requests_page': None,
        'ticket_requests_paginator': None,
        'ticket_requests_is_paginated': False,
        'ticket_requests_page_qs_prefix': '?',
        'ticket_requests_page_size': per_page,
        'ticket_requests_total': 0,
        'ticket_requests_page_param': page_param,
        'ticket_requests_open': False,
    }
    if not team:
        return empty
    paginator = Paginator(
        TicketRequest.objects.filter(team=team).order_by('-time'),
        per_page,
    )
    page_obj = paginator.get_page(request.GET.get(page_param) or 1)
    qs = request.GET.copy()
    try:
        qs.pop(page_param, None)
    except Exception:
        pass
    rest = qs.urlencode()
    return {
        'ticket_requests_page': page_obj,
        'ticket_requests_paginator': paginator,
        'ticket_requests_is_paginated': paginator.num_pages > 1,
        'ticket_requests_page_qs_prefix': ('?' + rest + '&') if rest else '?',
        'ticket_requests_page_size': per_page,
        'ticket_requests_total': paginator.count,
        'ticket_requests_page_param': page_param,
        'ticket_requests_open': bool(request.GET.get(page_param)),
    }


def _team_ticket_requests_ajax_response(request, team):
    """Partial HTML for ticket-request history pagination (no full page reload)."""
    ctx = _team_ticket_requests_page(request, team)
    if not ctx.get('ticket_requests_page'):
        return JsonResponse({'html': ''})
    html = render(request, 'ui/team_ticket_requests.html', ctx).content.decode('utf-8')
    page_obj = ctx['ticket_requests_page']
    return JsonResponse({
        'html': html,
        'page': page_obj.number,
        'has_previous': page_obj.has_previous(),
        'has_next': page_obj.has_next(),
        'num_pages': ctx['ticket_requests_paginator'].num_pages,
        'total': ctx['ticket_requests_total'],
    })


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
    team = request.user.profile.team_on
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' and request.GET.get('tr_page'):
        if not team:
            return JsonResponse({'html': ''})
        return _team_ticket_requests_ajax_response(request, team)
    ctx = _new_team_ui_context(request)
    ctx['team_section'] = 'hub'
    ctx['page_title'] = 'Команда'
    if team:
        ctx.update(_team_ticket_requests_page(request, team))
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


@never_cache
@require_http_methods(['GET'])
def new_pay_page(request):
    """Public team-ticket checkout with login/team gating only at purchase time.

    Доступна без логина: гостю предлагаем войти, авторизованному без команды —
    создать или вступить, чтобы купить билет для команды.
    """
    team = None
    payment_teams = []
    telegram_linked = False
    if request.user.is_authenticated and has_profile(request.user):
        team = request.user.profile.team_on if has_team(request.user) else None
        payment_teams = list(
            Team.objects.filter(member_links__profile=request.user.profile)
            .distinct()
            .order_by('visible_name', 'name')
        )
        telegram_linked = bool(
            request.user.profile.telegram_verified and request.user.profile.telegram_user_id
        )
    recent_requests = TicketRequest.recent_for_team(team) if team else []
    ticket_price_int = ticket_unit_price_for(team, RUSSIAN_CARD)
    ticket_price_amd = ticket_unit_price_for(team, INTERNATIONAL_CARD)
    from games.tribute_config import (
        configured_product,
        merchant_public_copy,
        tribute_checkout_enabled,
    )
    from games.tribute_service import team_is_discount_eligible

    regular_product = configured_product('regular')
    discount_product = configured_product('discount')
    selected_product = discount_product if team and team_is_discount_eligible(team) else regular_product
    tribute_seller, tribute_seller_url = merchant_public_copy()
    payment_team_options = []
    for option_team in payment_teams:
        option_product = discount_product if team_is_discount_eligible(option_team) else regular_product
        payment_team_options.append({
            'team': option_team,
            'ticket_price': ticket_unit_price_for(option_team, RUSSIAN_CARD),
            'ticket_price_amd': ticket_unit_price_for(option_team, INTERNATIONAL_CARD),
            'tribute_kind': option_product.kind if option_product else '',
            'tribute_amount': str(option_product.amount_major) if option_product else '',
            'tribute_currency': option_product.currency if option_product else '',
        })
    return render(request, 'ui/pay.html', {
        'team': team,
        'payment_team_options': payment_team_options,
        'ticket_price': ticket_price_int,
        'ticket_price_display': '{:,}'.format(ticket_price_int).replace(',', ' '),
        'ticket_price_amd': ticket_price_amd,
        'ticket_price_amd_display': '{:,}'.format(ticket_price_amd).replace(',', ' '),
        'team_tickets': team.tickets if team else 0,
        'recent_ticket_requests': recent_requests,
        'telegram_linked': telegram_linked,
        'telegram_username': request.user.profile.telegram_username if telegram_linked else '',
        'tribute_enabled': tribute_checkout_enabled(),
        'tribute_product': selected_product or regular_product,
        'tribute_regular_product': regular_product,
        'tribute_discount_product': discount_product,
        'tribute_seller': tribute_seller,
        'tribute_seller_url': tribute_seller_url,
        'page_title': 'Оплата',
        **_project_urls_context(NEW_UI_PROJECT),
        **_main_team_page_urls(),
    })


def _selected_payment_team(request, *, require_explicit=False):
    if not has_profile(request.user) or not has_team(request.user):
        return None
    raw_team_id = (request.POST.get('team_id') or '').strip()
    if not raw_team_id and not require_explicit:
        return request.user.profile.team_on
    if not raw_team_id:
        return None
    membership = (
        ProfileTeamMembership.objects.select_related('team')
        .filter(profile=request.user.profile, team_id=raw_team_id)
        .first()
    )
    return membership.team if membership else None


@login_required
@require_http_methods(['POST'])
def new_telegram_link_start(request):
    if not has_profile(request.user):
        messages.error(request, 'Сначала создайте профиль Inter Oves.')
        return redirect('/pay/')
    from games.telegram_linking import (
        TelegramLinkError,
        create_link_token,
        telegram_deep_link,
    )

    _row, raw_token = create_link_token(request.user)
    try:
        deep_link = telegram_deep_link(raw_token)
    except TelegramLinkError as exc:
        messages.error(request, exc.message)
        return redirect('/pay/')
    return redirect(deep_link)


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

    team = _selected_payment_team(request)
    if team is None:
        return JsonResponse(
            {'status': 'error', 'reason': 'team_forbidden', 'message': 'Выбранная команда недоступна.'},
            status=403,
        )
    try:
        tickets = int((request.POST.get('tickets') or '').strip())
    except Exception:
        tickets = 0
    if tickets < 1 or tickets > 20:
        return JsonResponse(
            {'status': 'error', 'reason': 'tickets', 'message': 'Введите число билетов от 1 до 20.'},
            status=400,
        )

    route = ticket_route_for(RUSSIAN_CARD)
    amount_rub = ticket_amount_for(team, route.key, tickets)

    ticket_request = None
    try:
        ticket_request = TicketRequest.objects.create(
            team=team,
            created_by=request.user,
            metrika_client_id=(request.COOKIES.get('_ym_uid') or '').strip()[:64],
            money=amount_rub,
            tickets=tickets,
            status='Pending',
            currency=route.currency,
            payment_provider=route.provider,
            merchant=route.merchant,
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
                    'message': 'Сейчас не получается создать платёж. Напишите Андрею в Telegram: https://t.me/andrewgark',
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
                    'message': 'Сейчас не получается создать платёж. Напишите Андрею в Telegram: https://t.me/andrewgark',
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
        'ticket_request_id': ticket_request.id,
        'analytics_events': [
            yandex_goal_payload(
                YANDEX_GOAL_TICKET_CHECKOUT,
                key='ticket_checkout:{}'.format(ticket_request.id),
                ack=analytics_ack_payload(YANDEX_GOAL_TICKET_CHECKOUT, ticket_request.id),
            ),
        ],
        'status_url': request.build_absolute_uri(
            reverse('new_ticket_payment_status', kwargs={'ticket_request_id': ticket_request.id})
        ),
    })


@require_http_methods(['POST'])
def new_create_crypto_ticket_payment(request):
    """Create TicketRequest + NOWPayments invoice; returns embed URL for crypto widget."""
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

    team = _selected_payment_team(request)
    if team is None:
        return JsonResponse(
            {'status': 'error', 'reason': 'team_forbidden', 'message': 'Выбранная команда недоступна.'},
            status=403,
        )
    try:
        tickets = int((request.POST.get('tickets') or '').strip())
    except Exception:
        tickets = 0
    if tickets < 1 or tickets > 20:
        return JsonResponse(
            {'status': 'error', 'reason': 'tickets', 'message': 'Введите число билетов от 1 до 20.'},
            status=400,
        )

    route = ticket_route_for(CRYPTO)
    amount_rub = ticket_amount_for(team, route.key, tickets)

    ticket_request = None
    try:
        ticket_request = TicketRequest.objects.create(
            team=team,
            created_by=request.user,
            metrika_client_id=(request.COOKIES.get('_ym_uid') or '').strip()[:64],
            money=amount_rub,
            tickets=tickets,
            status='Pending',
            currency=route.currency,
            payment_provider=route.provider,
            merchant=route.merchant,
        )

        team_label = (getattr(team, 'visible_name', None) or getattr(team, 'name', None) or str(team.pk))
        payment_description = f'Билеты для команды {team_label} (request {ticket_request.id})'
        return_url = request.build_absolute_uri('/pay/?payment=crypto_return')
        ipn_url = nowpayments_ipn_callback_url()

        invoice = nowpayments_create_invoice(
            price_amount=amount_rub,
            price_currency='rub',
            order_id=str(ticket_request.id),
            order_description=payment_description,
            ipn_callback_url=ipn_url,
            success_url=return_url,
            cancel_url=return_url,
        )
        invoice_id = invoice.get('id') or invoice.get('invoice_id')
        ticket_request.nowpayments_id = str(invoice_id)
        ticket_request.save(update_fields=['nowpayments_id'])
        invoice_url = invoice.get('invoice_url') or ''
        embed = embed_url_for_invoice(invoice_id)
    except RuntimeError as exc:
        msg = str(exc)
        if ticket_request is not None and 'Missing NOWPayments' in msg:
            logger.error('new_create_crypto_ticket_payment: %s', exc)
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'nowpayments_config',
                    'message': 'Сейчас не получается создать платёж. Напишите Андрею в Telegram: https://t.me/andrewgark',
                },
                status=503,
            )
        logger.exception(
            'new_create_crypto_ticket_payment failed ticket_request_id=%s team_id=%s amount_rub=%s',
            getattr(ticket_request, 'id', None),
            team.pk,
            amount_rub,
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
        # Avoid HTTP 502: some proxies replace the JSON body with an HTML gateway page.
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'nowpayments',
                'message': 'Не получилось создать крипто-платёж. Попробуйте позже.',
            },
            status=503,
        )
    except Exception:
        logger.exception(
            'new_create_crypto_ticket_payment failed ticket_request_id=%s team_id=%s amount_rub=%s',
            getattr(ticket_request, 'id', None),
            team.pk,
            amount_rub,
        )
        if ticket_request is None:
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'db',
                    'message': 'Сейчас не получается создать платёж. Напишите Андрею в Telegram: https://t.me/andrewgark',
                },
                status=500,
            )
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'nowpayments',
                'message': 'Не получилось создать крипто-платёж. Попробуйте позже.',
            },
            status=503,
        )

    return JsonResponse({
        'status': 'ok',
        'invoice_id': str(invoice_id),
        'invoice_url': invoice_url,
        'embed_url': embed,
        'return_url': return_url,
        'ticket_request_id': ticket_request.id,
        'analytics_events': [
            yandex_goal_payload(
                YANDEX_GOAL_TICKET_CHECKOUT,
                key='ticket_checkout:{}'.format(ticket_request.id),
                ack=analytics_ack_payload(YANDEX_GOAL_TICKET_CHECKOUT, ticket_request.id),
            ),
        ],
        'status_url': request.build_absolute_uri(
            reverse('new_ticket_payment_status', kwargs={'ticket_request_id': ticket_request.id})
        ),
    })


@require_http_methods(['POST'])
def new_create_tribute_ticket_payment(request):
    """Create/reuse one unambiguous Digital Product intent and return its fixed webLink."""
    if not request.user.is_authenticated:
        return JsonResponse(
            {'status': 'error', 'reason': 'login', 'message': 'Сессия истекла. Войдите снова.'},
            status=401,
        )
    if not has_profile(request.user) or not has_team(request.user):
        return JsonResponse(
            {'status': 'error', 'reason': 'team', 'message': 'Нужен профиль и команда.'},
            status=403,
        )
    try:
        tickets = int(request.POST.get('tickets') or '0')
    except (TypeError, ValueError):
        tickets = 0
    if tickets != 1:
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'quantity',
                'message': 'Через Tribute одна покупка начисляет ровно один билет.',
            },
            status=400,
        )
    team = _selected_payment_team(request, require_explicit=True)
    if team is None:
        return JsonResponse(
            {'status': 'error', 'reason': 'team_forbidden', 'message': 'Выбранная команда недоступна.'},
            status=403,
        )

    from games.tribute_service import TributeCheckoutError, create_or_reuse_intent

    try:
        intent, product, reused = create_or_reuse_intent(user=request.user, team=team)
    except TributeCheckoutError as exc:
        return JsonResponse(
            {'status': 'error', 'reason': exc.reason, 'message': exc.message},
            status=exc.status,
        )
    if not intent.ticket_request.metrika_client_id:
        intent.ticket_request.metrika_client_id = (request.COOKIES.get('_ym_uid') or '').strip()[:64]
        intent.ticket_request.save(update_fields=['metrika_client_id'])
    logger.info(
        'tribute_redirect intent_id=%s user_id=%s team_id=%s product_id=%s reused=%s',
        intent.pk, request.user.pk, team.pk, product.product_id, reused,
    )
    return JsonResponse({
        'status': 'ok',
        'payment_url': product.web_url,
        'intent_id': intent.pk,
        'ticket_request_id': intent.ticket_request_id,
        'reused': reused,
        'status_url': request.build_absolute_uri(
            reverse('new_ticket_payment_status', kwargs={'ticket_request_id': intent.ticket_request_id})
        ),
        'analytics_events': [
            yandex_goal_payload(
                YANDEX_GOAL_TICKET_CHECKOUT,
                key='ticket_checkout:{}'.format(intent.ticket_request_id),
                ack=analytics_ack_payload(YANDEX_GOAL_TICKET_CHECKOUT, intent.ticket_request_id),
            ),
        ],
    })

@require_http_methods(['GET', 'POST'])
def new_ticket_payment_status(request, ticket_request_id):
    """JSON status for a team's TicketRequest (long-polling friendly for crypto)."""
    if not request.user.is_authenticated or not has_profile(request.user) or not has_team(request.user):
        return JsonResponse({'status': 'error', 'reason': 'auth'}, status=401)

    ticket_request = (
        TicketRequest.objects.select_related('team')
        .filter(
            id=ticket_request_id,
            team__member_links__profile=request.user.profile,
        )
        .distinct()
        .first()
    )
    if not ticket_request:
        return JsonResponse({'status': 'error', 'reason': 'not_found'}, status=404)

    if request.method == 'POST':
        ack_key = (
            request.POST.get('analytics_ack')
            or request.headers.get('X-Interoves-Analytics-Ack')
            or ''
        ).strip()
        expected_key = 'ticket_purchase:{}'.format(ticket_request.id)
        if ack_key != expected_key:
            return JsonResponse({'status': 'error', 'reason': 'invalid_ack'}, status=400)
        if ticket_request.status != 'Accepted':
            return JsonResponse({'status': 'error', 'reason': 'not_accepted'}, status=409)
        if ticket_request.purchase_goal_sent_at is None:
            ticket_request.purchase_goal_sent_at = timezone.now()
            ticket_request.save(update_fields=['purchase_goal_sent_at'])
        return JsonResponse({'status': 'ok'})

    payload = {
        'status': ticket_request.status,
        'ticket_request_id': ticket_request.id,
        'tickets': ticket_request.tickets,
        'money': ticket_request.money,
        'currency': ticket_request.currency,
        'payment_provider': ticket_request.payment_provider,
        'merchant': ticket_request.merchant,
    }
    if ticket_request.status == 'Accepted' and ticket_request.purchase_goal_sent_at is None:
        payload['analytics_events'] = [ticket_purchase_goal_payload(ticket_request)]
    if ticket_request.status == 'Accepted' and ticket_request.team is not None:
        payload['team_tickets'] = ticket_request.team.tickets
    return JsonResponse(payload)


def new_donate_page(request):
    from games.donation_service import recent_donations_for_request, reject_stale_pending_donations

    reject_stale_pending_donations()
    return render(request, 'new/donate.html', {
        'recent_donations': recent_donations_for_request(request),
    })


@require_http_methods(['POST'])
def new_create_crypto_donation(request):
    """Create Donation + NOWPayments invoice; public (login optional)."""
    from games.donation_service import MIN_DONATION_RUB, donation_order_id, remember_donation_in_session

    raw = (request.POST.get('amount_rub') or request.POST.get('amount') or '').strip().replace(',', '.')
    try:
        amount_rub = int(float(raw))
    except (TypeError, ValueError):
        amount_rub = 0
    if amount_rub < MIN_DONATION_RUB:
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'amount',
                'message': 'Минимальная сумма доната — {} ₽.'.format(MIN_DONATION_RUB),
            },
            status=400,
        )
    if amount_rub > 1_000_000:
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'amount',
                'message': 'Слишком большая сумма. Укажите меньше миллиона рублей.',
            },
            status=400,
        )

    donation = None
    try:
        user = request.user if request.user.is_authenticated else None
        donation = Donation.objects.create(
            amount_rub=amount_rub,
            status='Pending',
            user=user,
        )
        remember_donation_in_session(request, donation.id)
        return_url = request.build_absolute_uri('/donate/?payment=crypto_return')
        ipn_url = nowpayments_ipn_callback_url()
        invoice = nowpayments_create_invoice(
            price_amount=amount_rub,
            price_currency='rub',
            order_id=donation_order_id(donation.id),
            order_description='Донат Inter Oves #{}'.format(donation.id),
            ipn_callback_url=ipn_url,
            success_url=return_url,
            cancel_url=return_url,
        )
        invoice_id = invoice.get('id') or invoice.get('invoice_id')
        donation.nowpayments_id = str(invoice_id)
        donation.save(update_fields=['nowpayments_id'])
        invoice_url = invoice.get('invoice_url') or ''
        embed = embed_url_for_invoice(invoice_id)
    except RuntimeError as exc:
        msg = str(exc)
        if donation is not None and 'Missing NOWPayments' in msg:
            logger.error('new_create_crypto_donation: %s', exc)
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'nowpayments_config',
                    'message': 'Сейчас не получается создать платёж. Напишите Андрею в Telegram: https://t.me/andrewgark',
                },
                status=503,
            )
        logger.exception(
            'new_create_crypto_donation failed donation_id=%s amount_rub=%s',
            getattr(donation, 'id', None),
            amount_rub,
        )
        if donation is None:
            return JsonResponse(
                {'status': 'error', 'reason': 'order', 'message': 'Не удалось создать донат. Попробуйте позже.'},
                status=500,
            )
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'nowpayments',
                'message': 'Не получилось создать крипто-платёж. Попробуйте позже.',
            },
            status=503,
        )
    except Exception:
        logger.exception(
            'new_create_crypto_donation failed donation_id=%s amount_rub=%s',
            getattr(donation, 'id', None),
            amount_rub,
        )
        if donation is None:
            return JsonResponse(
                {'status': 'error', 'reason': 'db', 'message': 'Не удалось сохранить донат. Попробуйте позже.'},
                status=500,
            )
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'nowpayments',
                'message': 'Не получилось создать крипто-платёж. Попробуйте позже.',
            },
            status=503,
        )

    return JsonResponse({
        'status': 'ok',
        'invoice_id': str(invoice_id),
        'invoice_url': invoice_url,
        'embed_url': embed,
        'return_url': return_url,
        'donation_id': donation.id,
        'amount_rub': donation.amount_rub,
        'donation_status': donation.status,
        'created_at': donation.created_at.strftime('%d.%m %H:%M') if donation.created_at else '',
        'public_token': donation.public_token,
        'status_url': request.build_absolute_uri(
            reverse('donate_status', kwargs={'public_token': donation.public_token})
        ),
    })


@require_http_methods(['GET'])
def new_donation_status(request, public_token):
    """Public JSON status for a donation (token acts as capability URL)."""
    donation = Donation.objects.filter(public_token=public_token).first()
    if not donation:
        return JsonResponse({'status': 'error', 'reason': 'not_found'}, status=404)

    payload = {
        'status': donation.status,
        'donation_id': donation.id,
        'amount_rub': donation.amount_rub,
    }
    if donation.status == 'Confirmed':
        payload['pay_amount'] = donation.pay_amount
        payload['pay_currency'] = donation.pay_currency
    return JsonResponse(payload)


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
