"""Main UI: hub, games folder, profile, team."""
import logging

from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.safestring import mark_safe

from games.daily_transitions import (
    next_daily_content_transition_for_game,
)

from django.utils import timezone



from games.alphabetty_daily import ALPHABETTY_GAME_ID
from games.daily.page.section import (
    current_number_for,
    is_scheduled_game,
    publish_at_for,
    scheduled_number_is_public,
    MOSCOW,
)
from games.daily.catalog.ladder import (
    LADDER_GAME_ID,
)
from games.sections.hub import (
    SECTION_HUB_META,
    create_game_link_context,
)
from games.models import (
    Attempt,
    AudioManager,
    Game,
    GameTaskGroup,
    ImageManager,
    PlayerCompletedGame,
    Project,
)
from games.replay import active_replay
from games.models import GameResultsSnapshot
from games.results.share import share_host_from_request
from games.site.views.pages.util import (
    effective_play_mode,
    has_profile,
    has_team,
    personal_play_mode_locked,
)
from games.results_snapshot import (
    snapshot_to_results_context,
)
from games.tutorials.ladder import (
    with_ladder_tutorial_link,
    ladder_tutorial_url,
)


from games.site.ui import (
    NEW_UI_PROJECT,
    NEW_UI_SECTIONS_PROJECT,
    PALINDROMES_GAME_ID,
    SECTION_RULES_GAME_IDS,
    _age_gate_context,
    _anon_key_from_request,
    _get_play_mode,
    _project_base,
    _project_urls_context,
    _root_project_game_redirect,
    _section_tutorial_html_for_game,
    _section_ui_context,
    _task_group_live_state_url,
)
from games.site.ui.game.access import (
    _maybe_registration_or_announce_response,
    _team_for_access,
)
from games.site.ui.game.progress import (
    _game_task_group_links,
    _initial_task_group_progress,
    _mark_locked_archive_rows,
    _task_group_page_nav_context,
    _task_group_replay_exit_url,
    _task_group_replay_url,
    _task_group_results_url,
    _task_group_rows_skeleton,
)

logger = logging.getLogger('games.views.new_ui')

def project_main_game_page(request, project_id, game_id):
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)
    base = _project_base(project.id)
    game = get_object_or_404(Game, id=game_id, project=project)
    from games.club.access.archive import reject_if_club_archive_blocked
    locked = reject_if_club_archive_blocked(request, game)
    if locked is not None:
        return locked

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
    task_group_rows, task_group_progress_context = _initial_task_group_progress(
        request,
        game,
        play_mode,
        task_groups,
        task_group_rows,
        mode=mode,
    )

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
        **task_group_progress_context,
        **_age_gate_context(game, back_url=(base + '/games/') if base else '/games/'),
    })


def project_results_page(request, project_id, game_id):
    from games.site.ui.results.compute import (
        _new_results_compute,
        _paginate_results_rows,
        _render_results_rows_partial,
        _results_actor_filter_types,
        _results_actor_filter_urls,
        _results_me_participants,
    )
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)
    base = _project_base(project.id)
    game = get_object_or_404(Game, id=game_id, project=project)
    team = request.user.profile.team_on if has_profile(request.user) else None
    if not game.has_access('see_results', mode='general', team=team):
        raise Http404()
    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    me_personal, me_anon_participant = _results_me_participants(request, play_mode)
    snap = GameResultsSnapshot.objects.filter(game=game, mode='general').first()
    if snap and snap.payload and request.GET.get('actors') is None:
        data = snapshot_to_results_context(game, snap.payload)
    else:
        data = _new_results_compute(game, mode='general', actor_types=_results_actor_filter_types(request))
    data = _paginate_results_rows(request, data, per_page=50)
    if request.GET.get('partial') == '1':
        return _render_results_rows_partial(
            request, data, mode='general', team=team,
            me_personal=me_personal, me_anon_participant=me_anon_participant,
        )
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
        'results_actor_filter_urls': _results_actor_filter_urls(request),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'section_games': [],
        'show_sections_nav': False,
        **_project_urls_context(project.id),
    })


def project_tournament_results_page(request, project_id, game_id):
    from games.site.ui.results.compute import (
        _new_results_compute,
        _paginate_results_rows,
        _render_results_rows_partial,
        _results_actor_filter_types,
        _results_actor_filter_urls,
    )
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)
    base = _project_base(project.id)
    game = get_object_or_404(Game, id=game_id, project=project)
    team = request.user.profile.team_on if has_profile(request.user) else None
    if not game.has_access('see_tournament_results', team=team):
        raise Http404()
    snap = GameResultsSnapshot.objects.filter(game=game, mode='tournament').first()
    if snap and snap.payload and request.GET.get('actors') is None:
        data = snapshot_to_results_context(game, snap.payload)
    else:
        data = _new_results_compute(game, mode='tournament', actor_types=_results_actor_filter_types(request))
    data = _paginate_results_rows(request, data, per_page=50)
    if request.GET.get('partial') == '1':
        return _render_results_rows_partial(request, data, mode='tournament', team=team)
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
        'results_actor_filter_urls': _results_actor_filter_urls(request),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'section_games': [],
        'show_sections_nav': False,
        **_project_urls_context(project.id),
    })


def project_task_group_page(request, project_id, game_id, task_group_number):
    from games.site.ui.play.context import build_task_group_task_context_dicts
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)
    base = _project_base(project.id)
    game = get_object_or_404(Game, id=game_id, project=project)
    if (
        is_scheduled_game(game.id)
        and not scheduled_number_is_public(game, task_group_number)
        and not request.user.is_staff
    ):
        raise Http404()

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
    if placement.is_deferred and not request.user.is_staff:
        raise Http404()
    task_group = placement.task_group
    replay_slot = active_replay(
        request=request,
        game=game,
        task_group=task_group,
        team=team,
        user=user,
        anon_key=anon_key,
    )
    actor_filter = {'team': team, 'user': user, 'anon_key': anon_key}
    official_completed = PlayerCompletedGame.objects.filter(
        game=game,
        task_group=task_group,
        result=PlayerCompletedGame.RESULT_SOLVED,
        **actor_filter,
    ).exists()
    prev_tg, next_tg = GameTaskGroup.prev_next_for(game, placement)
    tasks = sorted(task_group.tasks.visible(), key=lambda t: t.key_sort())
    ctx_dicts = build_task_group_task_context_dicts(
        game, task_group, tasks, team, user, anon_key, mode,
        placement=placement, replay_slot=replay_slot, request=request,
    )
    return render(request, 'ui/task_group.html', {
        'project': project,
        'game': game,
        'task_group': task_group,
        'tasks': tasks,
        'attempts_info_by_task_id': ctx_dicts['attempts_info_by_task_id'],
        'replacements_lines_data': ctx_dicts['replacements_lines_data'],
        'html_forms_data': ctx_dicts['html_forms_data'],
        'word_salad_data': ctx_dicts['word_salad_data'],
        'raddle_data': ctx_dicts['raddle_data'],
        'proportions_chips': ctx_dicts['proportions_chips'],
        'wall_max_points_meta_by_task_id': ctx_dicts['wall_max_points_meta_by_task_id'],
        'likes_meta_by_task_id': ctx_dicts['likes_meta_by_task_id'],
        'task_ui_by_task_id': ctx_dicts['task_ui_by_task_id'],
        'gameplay_context_tokens': ctx_dicts['gameplay_context_tokens'],
        'can_like': True,
        'has_profile_user': has_profile(request.user),
        'mode': mode,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'replay_slot': replay_slot,
        'replay_active': replay_slot is not None,
        'replay_completed': bool(replay_slot and replay_slot.status == 'completed'),
        'official_completed': official_completed,
        'anon_key': anon_key,
        'team': team,
        'show_palindrome_rules': False,
        'section_rules_type': None,
        'section_tutorial_html': None,
        'prev_task_group_url': '{}/games/{}/{}/'.format(base, game.id, prev_tg.number) if prev_tg else None,
        'next_task_group_url': '{}/games/{}/{}/'.format(base, game.id, next_tg.number) if next_tg else None,
        'task_group_results_url': _task_group_results_url(game, placement.number, project_base=base),
        'task_group_live_state_url': _task_group_live_state_url(game),
        'replay_url': _task_group_replay_url(game, placement.number, project_base=base),
        'replay_exit_url': _task_group_replay_exit_url(game, placement.number, project_base=base),
        'task_group_results_allowed': game.has_access('see_results', mode='general', team=team),
        'tg_number': placement.number,
        'tg_name': placement.name,
        'share_host': share_host_from_request(request),
        # Keep the single daily-game page in sync with the section card on
        # the homepage and the section archive.  Do not fall back to
        # ``game.theme`` here: for daily games it is legacy metadata and can
        # describe a different aspect of the format.
        'section_tagline': (
            SECTION_HUB_META.get(game.id, {}).get('description') or ''
            if game.project_id == NEW_UI_SECTIONS_PROJECT
            else ''
        ),
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


def new_section_game_page(request, game_id):
    """Страница раздела (игра из project sections) в новом UI: правила при необходимости + список групп заданий."""
    if game_id == LADDER_GAME_ID:
        return redirect('ui_ladder_hub', permanent=True)
    if game_id == ALPHABETTY_GAME_ID:
        return redirect('ui_alphabetty_hub', permanent=True)
    return _render_section_game_page(request, game_id)


def _render_section_game_page(request, game_id):
    from games.site.ui.hub.cards import (
        _add_daily_completion_statuses,
        _daily_archive_context,
        _hub_section_task_group_links,
        _ladder_task_group_rows,
    )
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
    task_groups = list(_hub_section_task_group_links(game))
    archive_context = _daily_archive_context(request, game, task_groups)
    if archive_context.get('daily_archive'):
        selected_year, selected_month = archive_context['daily_archive_selected']
        task_groups = [
            link for link in task_groups
            if (
                publish_at_for(game, link.number).astimezone(MOSCOW).date().year == selected_year
                and publish_at_for(game, link.number).astimezone(MOSCOW).date().month == selected_month
            )
        ]
    if meta.get('archive_item_label'):
        task_group_rows = _ladder_task_group_rows(
            task_groups,
            game,
            today_number=today_number,
            today_prefix=meta.get('archive_today_prefix', 'Сегодня'),
            item_label=meta['archive_item_label'],
        )
        task_groups_heading = False
        task_groups_empty_text = meta.get(
            'archive_empty_text',
            'Скоро появятся — следите за обновлениями.',
        )
    else:
        task_group_rows = _task_group_rows_skeleton(task_groups, game)
        task_groups_heading = _task_group_page_nav_context(game)['task_group_pager_label']
        task_groups_empty_text = 'В этом разделе пока нет заданий. Добавьте их в админке.'
    task_group_rows, task_group_progress_context = _initial_task_group_progress(
        request,
        game,
        play_mode,
        task_groups,
        task_group_rows,
    )
    _mark_locked_archive_rows(request, game, task_group_rows)
    completed_numbers = [
        row['number'] for row in task_group_rows if row.get('is_fully_solved')
    ]
    archive_context = _daily_archive_context(
        request, game, _hub_section_task_group_links(game),
        completed_numbers=completed_numbers,
        status_by_key={
            str(row['number']): ('solved' if row.get('is_fully_solved') else 'partial')
            for row in task_group_rows
            if row.get('row_class') in ('new-task--partial', 'new-task--solved')
        },
        locked_keys={row['number'] for row in task_group_rows if row.get('is_archive_locked')},
    )
    _add_daily_completion_statuses(
        request, game, task_group_rows, _hub_section_task_group_links(game),
    )

    section_today_play_url = None
    section_today_cta_label = ''
    if (
        today_number is not None
        and scheduled_number_is_public(game, today_number)
        and meta.get('cta_today')
    ):
        has_today = any(str(p.number) == str(today_number) for p in task_groups)
        if has_today or game_id == LADDER_GAME_ID:
            from games.sections.paths import section_last_path
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
    if game_id == LADDER_GAME_ID:
        tutorial_url = ladder_tutorial_url()
        section_task_groups_rules_html = with_ladder_tutorial_link(section_task_groups_rules_html, tutorial_url)
        section_tutorial_html = with_ladder_tutorial_link(section_tutorial_html, tutorial_url)
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
        **archive_context,
        'section_tagline': meta.get('description') or '',
        'ladder_today_number': today_number if game_id == LADDER_GAME_ID else None,
        'section_today_play_url': section_today_play_url,
        'section_today_cta_label': section_today_cta_label,
        'section_create_link': create_game_link_context(game_id),
        'back_url': '/',
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'team': team_for_access,
        'show_sections_nav': True,
        'live_next_transition_at': next_daily_content_transition_for_game(game),
        **_project_urls_context(NEW_UI_PROJECT),
        **_section_ui_context(game),
        **task_group_progress_context,
        **_age_gate_context(game, back_url='/'),
    })


def new_main_game_page(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    project_redirect = _root_project_game_redirect(request, game)
    if project_redirect is not None:
        return project_redirect
    if game.project_id != NEW_UI_PROJECT:
        raise Http404()
    from games.club.access.archive import reject_if_club_archive_blocked
    locked = reject_if_club_archive_blocked(request, game)
    if locked is not None:
        return locked

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
    task_group_rows, task_group_progress_context = _initial_task_group_progress(
        request,
        game,
        play_mode,
        task_groups,
        task_group_rows,
        mode=mode,
    )

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
        **task_group_progress_context,
        **_age_gate_context(game, back_url='/games/'),
    })



__all__ = [
    'project_main_game_page',
    'project_results_page',
    'project_tournament_results_page',
    'project_task_group_page',
    'new_section_game_page',
    '_render_section_game_page',
    'new_main_game_page',
]
