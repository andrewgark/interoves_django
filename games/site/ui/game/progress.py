"""Main UI: hub, games folder, profile, team."""
import logging

from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse


from django.db.models import Count, Q
from django.utils import timezone



from games.club.access.game import game_has_started
from games.daily.page.section import (
    is_scheduled_game,
    publish_at_for,
    visible_links,
    MOSCOW,
)
from games.daily.page.placement import is_random_daily_placement
from games.daily.catalog.ladder import (
    LADDER_GAME_ID,
)
from games.tasks.progress import compute_task_progress, merge_task_group_progress_rows
from games.tasks.navigation import (
    neighbors_by_pk,
    play_url_for_task_group,
    replay_exit_url_for_task_group,
    replay_url_for_task_group,
    results_url_for_task_group,
    task_group_page_nav_context,
)
from games.models import (
    Attempt,
    Game,
    GameTaskGroup,
    Project,
    Task,
)
from games.puzzles.raddle.engine import (
    raddle_hub_result_for_actor,
)
from games.puzzles.salad.rules import (
    WORD_SALAD_GAME_ID,
    salad_hub_result_for_actor,
)
from games.site.views.pages.util import (
    effective_play_mode,
    has_profile,
)


from games.site.ui import (
    NEW_UI_PROJECT,
    NEW_UI_SECTIONS_PROJECT,
    _anon_key_from_request,
    _get_play_mode,
    _root_project_game_redirect,
    _ru_iz_punkt_word,
)

logger = logging.getLogger('games.views.new_ui')

def _compute_solved_task_ids(game, task_groups, team=None, user=None, anon_key=None, mode='general'):
    solved_task_ids, tg_to_task_ids, _task_result_points = compute_task_progress(
        game,
        task_groups,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        include_other_games=game.project_id == NEW_UI_SECTIONS_PROJECT,
    )

    return solved_task_ids, tg_to_task_ids


def _game_task_group_links(game):
    return GameTaskGroup.sorted_links(
        GameTaskGroup.objects.filter(game=game, is_deferred=False)
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
    return neighbors_by_pk(links, placement)


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
    return play_url_for_task_group(game, number, project_base=project_base)


def _task_group_results_url(game, number, *, project_base=''):
    return results_url_for_task_group(game, number, project_base=project_base)


def _task_group_replay_url(game, number, *, project_base=''):
    return replay_url_for_task_group(game, number, project_base=project_base)


def _task_group_replay_exit_url(game, number, *, project_base=''):
    return replay_exit_url_for_task_group(game, number, project_base=project_base)


def _task_group_page_nav_context(game, *, prev_tg=None, next_tg=None):
    """Подписи верхнего «назад к списку» и нижнего пейджера кругов."""
    return task_group_page_nav_context(
        game,
        previous=prev_tg,
        following=next_tg,
        sections_project_id=NEW_UI_SECTIONS_PROJECT,
        main_project_id=NEW_UI_PROJECT,
    )


def _task_group_results_nav_context(game, placement):
    """Navigation between adjacent daily task result pages."""
    if (
        not isinstance(placement, GameTaskGroup)
        or not is_scheduled_game(game.id)
        or is_random_daily_placement(placement)
    ):
        return {}
    links = list(visible_links(_game_task_group_links(game), game))
    prev_tg, next_tg = _neighbors_by_pk(links, placement)
    published_at = publish_at_for(game, placement.number)
    return {
        'daily_results_navigation': True,
        'daily_publish_date': published_at.astimezone(MOSCOW).date() if published_at else None,
        'prev_task_group_url': (
            _task_group_results_url(game, prev_tg.number) if prev_tg else None
        ),
        'next_task_group_url': (
            _task_group_results_url(game, next_tg.number) if next_tg else None
        ),
        **_task_group_page_nav_context(game, prev_tg=prev_tg, next_tg=next_tg),
    }


def _task_group_rows_skeleton(task_groups, game, *, project_base=''):
    """Task group list for game hub pages; actor progress is loaded separately."""
    task_groups = list(task_groups)
    from games.daily.difficulty.core import get_cached_game_difficulties
    difficulties = get_cached_game_difficulties(task_groups)
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
            'difficulty': difficulties.get(p.pk),
            'is_archive_locked': False,
        }
        for p in task_groups
    ]


def _mark_locked_archive_rows(request, game, rows):
    from games.club.access.archive import (
        desyatka_requires_club,
        scheduled_number_requires_club,
        user_can_access_scheduled_number,
    )
    for row in rows:
        row['is_archive_locked'] = (
            (scheduled_number_requires_club(game, row.get('number')) or desyatka_requires_club(game))
            and not row.get('is_fully_solved')
            and not user_can_access_scheduled_number(request.user, game, row.get('number'))
        )
    return rows


def _archive_nav_target(request, game, placement, href):
    """Return a navigation target with the same paywall semantics as a card."""
    from games.club.access.archive import user_can_access_scheduled_number
    if not placement:
        return None, False
    locked = not user_can_access_scheduled_number(request.user, game, placement.number)
    return ('/subscription/' if locked else href), locked


def _task_group_progress_payload(game, task_groups, *, team=None, user=None, anon_key=None, mode='general'):
    canonical_groups = [p.task_group for p in task_groups]
    solved_task_ids, tg_to_task_ids, task_result_points = compute_task_progress(
        game=game,
        task_groups=canonical_groups,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        include_other_games=game.project_id == NEW_UI_SECTIONS_PROJECT,
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
        attempts_infos = Attempt.manager.get_bulk_actor_attempts_infos(
            [task.id for task in tg_to_task.values()],
            team=team,
            user=user,
            anon_key=anon_key,
            mode=mode,
            game=None if include_other_games else game,
        )
        from games.daily_timing import timing_rows_for_task_groups
        timing_by_tg = timing_rows_for_task_groups(
            game=game,
            task_group_ids=tg_ids,
            user=user,
            anon_key=anon_key,
            team=team,
        )
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
                attempts_info=attempts_infos.get(task.id),
                timing_row=timing_by_tg.get(task.task_group_id),
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
        has_points = any(task_result_points.get(tid, 0) > 0 for tid in tg_task_ids)
        row_class = ''
        if p.n_tasks and n_solved >= p.n_tasks:
            row_class = 'new-task--solved'
        elif n_solved or has_points:
            row_class = 'new-task--partial'
        is_fully_solved = bool(p.n_tasks) and n_solved >= p.n_tasks
        # Пишем «N из M решено» только при частичном прогрессе (0 < N < M).
        if n_solved and p.n_tasks and n_solved < p.n_tasks:
            progress_text = '{} из {} {} решено'.format(
                n_solved, p.n_tasks, _ru_iz_punkt_word(p.n_tasks),
            )
        else:
            progress_text = None
        # Лесенка/Салатик: квадраты. Полные → зелёный; с ⬜ → жёлтый partial.
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
        from games.sections.paths import is_root_section_game, section_progress_path
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


def _merge_task_group_progress_rows(task_group_rows, progress_rows):
    """Apply an authoritative actor projection to already-built display rows."""
    return merge_task_group_progress_rows(task_group_rows, progress_rows)


def _initial_task_group_progress(
    request,
    game,
    play_mode,
    task_groups,
    task_group_rows,
    *,
    mode=None,
):
    """Render actor progress into the first response; retain lazy JSON as fallback."""
    progress_context = _game_page_progress_context(request, game, play_mode)
    if not progress_context['load_task_group_progress']:
        return task_group_rows, progress_context

    team, user, anon_key = _resolve_game_page_actor(request, play_mode)
    try:
        progress_rows = _task_group_progress_payload(
            game,
            task_groups,
            team=team,
            user=user,
            anon_key=anon_key,
            mode=mode or game.get_current_mode(Attempt(time=timezone.now())),
        )
    except Exception:
        logger.exception(
            'Initial task-group progress failed; using JSON fallback game=%s',
            game.id,
        )
        return task_group_rows, progress_context

    _merge_task_group_progress_rows(task_group_rows, progress_rows)
    _mark_locked_archive_rows(request, game, task_group_rows)
    progress_context['load_task_group_progress'] = False
    progress_context['task_group_progress_embedded'] = True
    return task_group_rows, progress_context


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
    project_redirect = _root_project_game_redirect(request, game, 'progress/')
    if project_redirect is not None:
        return project_redirect
    return _game_task_group_progress_response(request, game)


def project_game_task_group_progress(request, project_id, game_id):
    project = get_object_or_404(Project, id=(project_id or '').strip())
    game = get_object_or_404(Game, id=game_id, project=project)
    return _game_task_group_progress_response(request, game)


__all__ = [
    '_compute_solved_task_ids',
    '_game_task_group_links',
    '_player_visible_task_group_links',
    '_neighbors_by_pk',
    '_resolve_game_page_actor',
    '_play_url_for_task_group',
    '_task_group_results_url',
    '_task_group_replay_url',
    '_task_group_replay_exit_url',
    '_task_group_page_nav_context',
    '_task_group_results_nav_context',
    '_task_group_rows_skeleton',
    '_mark_locked_archive_rows',
    '_archive_nav_target',
    '_task_group_progress_payload',
    '_game_page_progress_context',
    '_merge_task_group_progress_rows',
    '_initial_task_group_progress',
    '_game_task_group_progress_response',
    'new_game_task_group_progress',
    'project_game_task_group_progress',
]
