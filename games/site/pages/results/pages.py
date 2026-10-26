"""Main UI: hub, games folder, profile, team."""
import logging

from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render

from games.daily.catalog.ladder import (
    LADDER_GAME_ID,
)
from games.daily.page.archive import (
    MONTH_NAMES,
)
from games.daily.page.section import (
    is_scheduled_game,
    scheduled_number_is_public,
)
from games.daily.projection.results import get_daily_results_adapter
from games.identity import has_profile
from games.models import (
    Game,
    GameResultsSnapshot,
    GameTaskGroup,
    Project,
    Task,
)
from games.results.ladder import (
    build_ladder_word_results_context,
)
from games.results.snapshots.snapshot import (
    snapshot_to_results_context,
)
from games.results.table.context import (
    results_actor_identity,
)
from games.site.pages import (
    NEW_UI_PROJECT,
    NEW_UI_SECTIONS_PROJECT,
    _get_play_mode,
    _project_base,
    _project_urls_context,
    _root_project_game_redirect,
)
from games.site.pages.game.access import _team_for_access
from games.site.pages.game.progress import (
    _play_url_for_task_group,
    _task_group_results_nav_context,
)
from games.site.pages.hub.cards import _sections_hub_url
from games.site.pages.results.compute import (
    _club_subscriber_user_ids,
    _new_results_compute,
    _paginate_results_rows,
    _public_exclusion_notice,
    _render_results_rows_partial,
    _results_actor_filter_types,
    _results_actor_filter_urls,
    _results_column_count,
    _results_me_participants,
)
from games.site.session.play_mode import effective_play_mode, personal_play_mode_locked
from games.task_group.result_details import (
    SaladResultHeader,
    SaladResultWord,
    chain_state_for_actor,
    latest_actor_task_state,
    raddle_ui_state_for_actor,
    set_current_result_header_answers,
    word_salad_release_breakdown,
)
from games.task_group.titles import task_group_page_title

logger = logging.getLogger('games.views.new_ui')

def results_page(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    project_redirect = _root_project_game_redirect(request, game, 'results/')
    if project_redirect is not None:
        return project_redirect
    if game.project_id != NEW_UI_PROJECT:
        raise Http404()
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    # Results are viewable without being logged in; permissions are enforced by access rules.
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
        'results_actor_filter_urls': _results_actor_filter_urls(request),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'show_sections_nav': True,
        **_project_urls_context(game.project_id),
    })


def tournament_results_page(request, game_id):
    game = get_object_or_404(Game, id=game_id)
    project_redirect = _root_project_game_redirect(request, game, 'tournament-results/')
    if project_redirect is not None:
        return project_redirect
    if game.project_id != NEW_UI_PROJECT:
        raise Http404()
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    # Tournament results are viewable without being logged in; permissions are enforced by access rules.
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
        'results_actor_filter_urls': _results_actor_filter_urls(request),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'show_sections_nav': True,
        **_project_urls_context(game.project_id),
    })


def section_results_page(request, game_id):
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

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    me_personal, me_anon_participant = _results_me_participants(request, play_mode)
    results_adapter = get_daily_results_adapter(game_id)
    results_variant = (
        results_adapter.aggregate_variant
        if results_adapter is not None
        else 'standard'
    )

    from games.results.snapshots.aggregate_snapshots import SnapshotVersionExpired
    from games.results.standings.aggregate_leaderboard import build_aggregate_page
    try:
        data = build_aggregate_page(request, game)
    except SnapshotVersionExpired:
        return HttpResponse(
            'Таблица обновилась. Обновите страницу.', status=409,
            headers={'X-Results-Reload': '1'},
        )
    aggregate_subscriber_ids = _club_subscriber_user_ids(
        row.get('actor') for row in data.get('aggregate_rows', [])
    )
    for row in data.get('aggregate_rows', []):
        actor = row.get('actor')
        row['is_club_subscriber'] = getattr(actor, 'user_id', None) in aggregate_subscriber_ids
    aggregate_column_labels = {}
    if results_variant == 'alphabetty':
        current_actor = team if play_mode == 'team' else (me_personal or me_anon_participant)
        current_actor_identity = results_actor_identity(current_actor)
        current_row = next(
            (
                row for row in data.get('aggregate_rows', [])
                if results_actor_identity(row.get('actor')) == current_actor_identity
            ),
            None,
        ) if current_actor_identity is not None else None
        for column in data.get('aggregate_columns', []):
            score = current_row.get('cells', {}).get(column.link.pk) if current_row else None
            tasks = getattr(column.link.task_group, 'result_tasks', ()) or ()
            task = tasks[0] if tasks else None
            answer = getattr(task, 'answer', '') if task else ''
            solved = score is not None and float(score or 0) > 0
            if task is not None:
                from games.puzzles.alphabetty.play import load_state
                raw_state = _latest_actor_task_state(game, task, current_actor)
                solved = solved or bool(raw_state and load_state(raw_state).get('won'))
            if solved and answer:
                aggregate_column_labels[column.link.pk] = answer

    def query_url(**overrides):
        params = request.GET.copy()
        # The progressive participant list is a client-side view state; any
        # filter/window change starts a fresh list from its first page.
        params.pop('loaded', None)
        if any(key in overrides for key in ('month', 'anchor', 'limit')):
            params.pop('snapshot', None)
        elif data.get('aggregate_snapshot_id'):
            params['snapshot'] = data['aggregate_snapshot_id']
        # The view token belongs to the current filter and visibility; only
        # progressive partial requests send it, from the page's data attribute.
        params.pop('snapshot_view', None)
        if 'anchor' not in overrides and data.get('aggregate_window_anchor') and not params.get('anchor'):
            params['anchor'] = data['aggregate_window_anchor']
        for key, value in overrides.items():
            if value is None:
                params.pop(key, None)
            else:
                params[key] = str(value)
        query = params.urlencode()
        return request.path + ('?' + query if query else '')

    selected_actor_types = data.get('aggregate_actor_types', {'user', 'team', 'anon'})
    actor_filter_defs = (
        ('user', 'Пользователи', 'ph-user'),
        ('team', 'Команды', 'ph-users'),
        ('anon', 'Анонимы', 'ph-detective'),
    )
    actor_filter_urls = []
    for kind, label, icon in actor_filter_defs:
        next_types = set(selected_actor_types)
        if kind in next_types:
            next_types.remove(kind)
        else:
            next_types.add(kind)
        actor_filter_urls.append({
            'label': label, 'icon': icon, 'active': kind in selected_actor_types,
            'title': ('Скрыть ' if kind in selected_actor_types else 'Показать ') + label.lower(),
            'url': query_url(actors=','.join(value for value in ('user', 'team', 'anon') if value in next_types), page=None),
        })

    def month_url(month):
        return query_url(
            month=month.strftime('%Y-%m'), page=None,
            anchor=None, limit=None, loaded=None,
        )

    data.update({
        'mode': 'general',
        'section_results': True,
        'is_ladder_results': results_variant == 'ladder',
        'results_variant': results_variant,
        'game': game,
        'team': team,
        'me_personal': me_personal,
        'me_anon_participant': me_anon_participant,
        'page_title': 'Результаты: {}'.format(game.get_no_html_name() if hasattr(game, 'get_no_html_name') else game.name),
        'limit_urls': {limit: query_url(limit=limit, page=None) for limit in (10, 20, 30)},
        'aggregate_month_label': (
            '{} {}'.format(
                MONTH_NAMES[data['aggregate_month'].month],
                data['aggregate_month'].year,
            ) if data.get('aggregate_month') else None
        ),
        'aggregate_previous_month_url': month_url(data['aggregate_previous_month']) if data.get('aggregate_previous_month') else None,
        'aggregate_next_month_url': month_url(data['aggregate_next_month']) if data.get('aggregate_next_month') else None,
        'aggregate_actor_filter_urls': actor_filter_urls,
        'aggregate_column_labels': aggregate_column_labels,
        'aggregate_show_attempts': data.get('aggregate_show_attempts', False),
        'aggregate_sort_urls': {
            value: query_url(sort=value, page=None)
            for value in ('attempts', 'time')
        },
        'aggregate_refresh_url': query_url(snapshot=None, page=None, loaded=None),
        'older_url': query_url(anchor=data['aggregate_older_anchor'], page=None) if data['aggregate_older_anchor'] else None,
        'newer_url': query_url(anchor=data['aggregate_newer_anchor'], page=None) if data['aggregate_newer_anchor'] else None,
        'previous_page_url': query_url(page=data['aggregate_page'].previous_page_number()) if data['aggregate_page'].has_previous() else None,
        'next_page_url': query_url(page=data['aggregate_page'].next_page_number()) if data['aggregate_page'].has_next() else None,
        'back_url': _sections_hub_url(game.id),
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'show_sections_nav': True,
        **_project_urls_context(NEW_UI_PROJECT),
    })
    if request.GET.get('partial') == '1':
        return render(request, 'site/partials/aggregate_results_rows.html', {
            **data,
        })
    return render(request, 'site/aggregate_results.html', {
        **data,
    })


def _render_task_group_results_page(request, game, number, back_url, placement=None):
    """Shared renderer for a results table scoped to one task group."""
    team = request.user.profile.team_on if has_profile(request.user) else None
    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    me_personal, me_anon_participant = _results_me_participants(request, play_mode)
    results_adapter = get_daily_results_adapter(game.id)
    results_variant = (
        results_adapter.task_variant
        if results_adapter is not None
        else 'standard'
    )
    alphabetty_sort = 'time' if results_variant == 'alphabetty' and request.GET.get('sort') == 'time' else 'attempts'
    data = _new_results_compute(
        game, mode='general', task_group_number=number,
        alphabetty_sort=alphabetty_sort,
        actor_types=_results_actor_filter_types(request),
    )
    if results_variant == 'salad_words':
        data = _word_salad_release_breakdown(data, game, number)
    data = _set_current_result_header_answers(
        data, me_personal or me_anon_participant or team, game=game,
    )
    if placement is None:
        placement = GameTaskGroup.objects.filter(
            game=game, number=str(number),
        ).select_related('task_group').first()
    results_title = task_group_page_title(game, placement) if placement else '{} №{}'.format(game.name, number)
    data = _paginate_results_rows(request, data, per_page=50)
    if request.GET.get('partial') == '1':
        return _render_results_rows_partial(
            request, data, mode='general', results_variant=results_variant,
            team=team, me_personal=me_personal,
            me_anon_participant=me_anon_participant,
            show_solve_duration=True,
            show_alphabetty_detail=results_variant == 'alphabetty',
        )
    return render(request, 'ui/results.html', {
        'mode': 'general',
        'section_results': True,
        'results_variant': results_variant,
        'game': game,
        'team': team,
        'me_personal': me_personal,
        'me_anon_participant': me_anon_participant,
        'back_url': back_url,
        'show_solve_duration': True,
        'alphabetty_sort': alphabetty_sort,
        'show_alphabetty_detail': results_variant == 'alphabetty',
        **data,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'page_title': results_title,
        'results_actor_filter_urls': _results_actor_filter_urls(request),
        'results_exclusion_notice': _public_exclusion_notice(
            request, game, placement.task_group if placement else None, surface='results',
        ),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'show_sections_nav': True,
        **_project_urls_context(game.project_id),
        **_task_group_results_nav_context(game, placement),
    })


_SaladResultHeader = SaladResultHeader
_SaladResultWord = SaladResultWord


def _chain_state_for_actor(game, task, actor):
    return chain_state_for_actor(game, task, actor)


def _raddle_ui_state_for_actor(
    game, task, *, team=None, user=None, anon_key=None,
    mode='general', replay_slot=None,
):
    return raddle_ui_state_for_actor(
        game,
        task,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
    )


def _latest_actor_task_state(game, task, actor):
    return latest_actor_task_state(game, task, actor)


def _set_current_result_header_answers(data, actor, game=None):
    return set_current_result_header_answers(data, actor, game=game)


def _word_salad_release_breakdown(data, game, number):
    return word_salad_release_breakdown(data, game, number)


def section_task_results_page(request, game_id, number):
    """Results for one task group of a sections-project game."""
    from games.sections.paths import section_play_path

    project = Project.objects.filter(id=NEW_UI_SECTIONS_PROJECT).first()
    if not project:
        raise Http404()
    game = Game.objects.filter(project=project, id=game_id).first()
    if not game:
        raise Http404()
    from games.daily.page.placement import resolve_accessible_daily_placement
    play_segment = number
    placement = resolve_accessible_daily_placement(game, play_segment, user=request.user)
    if placement is None:
        raise Http404()
    number = placement.number
    from games.club.access.archive import reject_if_club_archive_blocked
    locked = reject_if_club_archive_blocked(request, game, number=number)
    if locked is not None:
        return locked
    team = request.user.profile.team_on if has_profile(request.user) else None
    if not game.has_access('see_results', mode='general', team=team):
        raise Http404()
    return _render_task_group_results_page(
        request, game, number, section_play_path(game.id, play_segment),
        placement=placement,
    )


def game_task_results_page(request, game_id, number, project_id=None):
    """Results for one task group in a project-scoped game."""
    expected_project_id = project_id or NEW_UI_PROJECT
    if project_id is None:
        root_game = get_object_or_404(Game, id=game_id)
        project_redirect = _root_project_game_redirect(
            request, root_game, '{}/results/'.format(number),
        )
        if project_redirect is not None:
            return project_redirect
    game = get_object_or_404(Game, id=game_id, project_id=expected_project_id)
    if is_scheduled_game(game.id):
        from games.daily.page.placement import resolve_accessible_daily_placement

        placement = resolve_accessible_daily_placement(game, number, user=request.user)
        if placement is None:
            raise Http404()
    else:
        placement = GameTaskGroup.objects.filter(
            game=game, number=str(number),
        ).select_related('task_group').first()
        if placement is None:
            raise Http404()
    from games.club.access.archive import reject_if_club_archive_blocked
    locked = reject_if_club_archive_blocked(request, game, number=placement.number)
    if locked is not None:
        return locked
    team = _team_for_access(request)
    if not game.has_access('see_results', mode='general', team=team):
        raise Http404()
    base = _project_base(game.project_id)
    return _render_task_group_results_page(
        request, game, placement.number,
        '{}/games/{}/{}/'.format(base, game.id, number),
        placement=placement,
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


def ladder_word_results_page(request, task_group_number):
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

    from types import SimpleNamespace

    from games.club.offers.ladder import get_offer_by_share_hash, is_share_hash_segment
    from games.daily.page.placement import resolve_accessible_daily_placement

    ladder_offer = None
    if is_share_hash_segment(str(task_group_number)):
        ladder_offer = get_offer_by_share_hash(str(task_group_number))
        if ladder_offer is None:
            placement = resolve_accessible_daily_placement(
                game, task_group_number, user=request.user,
            )
            if placement is None:
                raise Http404()
        else:
            from games.club.offers.ladder import can_access_offer_hash
            if not can_access_offer_hash(ladder_offer, request.user):
                raise Http404()
            if ladder_offer.accepted_link_id:
                if ladder_offer.accepted_link.is_deferred:
                    raise Http404()
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
        placement = resolve_accessible_daily_placement(
            game, task_group_number, user=request.user,
        )
        if not placement:
            raise Http404()
        from games.club.access.archive import reject_if_club_archive_blocked
        locked = reject_if_club_archive_blocked(request, game, number=placement.number)
        if locked is not None:
            return locked

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
        ladder_title = task_group_page_title(game, placement)
        back_url = _play_url_for_task_group(game, placement.number)

    def build_word_results_data():
        data = build_ladder_word_results_context(
            game, placement, task,
            actor_types=_results_actor_filter_types(request),
        )
        data = _set_current_result_header_answers(
            data, me_personal or me_anon_participant or team, game=game,
        )
        from games.daily.page.section import publish_at_for
        from games.results.standings.leaderboard import apply_release_policy
        return apply_release_policy(
            data, game=game, task_group=placement.task_group,
            published_at=(
                publish_at_for(game, placement.number)
                if scheduled_number_is_public(game, placement.number) else None
            ),
            result_times=data.get('team_to_max_best_time', {}),
        )

    if request.GET.get('partial') == '1':
        data = build_word_results_data()
        data = _paginate_results_rows(request, data, per_page=progressive_page_size)
        return render(request, 'site/partials/results_rows.html', {
            'mode': 'general',
            'section_results': True,
            'is_ladder_results': True,
            'is_ladder_word_results': True,
            'results_variant': 'ladder_words',
            'show_solve_duration': True,
            'game': game,
            'team': team,
            'me_personal': me_personal,
            'me_anon_participant': me_anon_participant,
            **data,
        })

    data = build_word_results_data()
    data = _paginate_results_rows(request, data, per_page=progressive_page_size)
    data['results_column_count'] = _results_column_count(
        data.get('task_groups'), mode='general'
    )

    return render(request, 'ui/results.html', {
        'mode': 'general',
        'section_results': True,
        'is_ladder_results': True,
        'is_ladder_word_results': True,
        'results_variant': 'ladder_words',
        'show_solve_duration': True,
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
        'page_title': ladder_title,
        'results_actor_filter_urls': _results_actor_filter_urls(request),
        'results_exclusion_notice': _public_exclusion_notice(
            request, game, getattr(placement, 'task_group', None), surface='results',
        ),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'show_sections_nav': True,
        **_project_urls_context(NEW_UI_PROJECT),
        **_task_group_results_nav_context(game, placement),
    })


__all__ = [
    'new_results_page',
    'new_tournament_results_page',
    'new_section_results_page',
    '_render_task_group_results_page',
    '_SaladResultHeader',
    '_SaladResultWord',
    '_chain_state_for_actor',
    '_raddle_ui_state_for_actor',
    '_latest_actor_task_state',
    '_set_current_result_header_answers',
    '_word_salad_release_breakdown',
    'new_section_task_results_page',
    'new_game_task_results_page',
    '_ladder_raddle_task_for_placement',
    'new_ladder_word_results_page',
]

# Names kept for existing imports and patches.
new_game_task_results_page = game_task_results_page
new_ladder_word_results_page = ladder_word_results_page
new_results_page = results_page
new_section_results_page = section_results_page
new_section_task_results_page = section_task_results_page
new_tournament_results_page = tournament_results_page
