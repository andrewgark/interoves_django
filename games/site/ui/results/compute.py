"""Main UI: hub, games folder, profile, team."""
import logging

from django.core.cache import cache


from django.utils import timezone



from games.daily.catalog.alphabetty import ALPHABETTY_GAME_ID
from games.results.table.context import (
    attach_results_club_badges,
    club_subscriber_user_ids,
    empty_results_rows_context,
    results_actor_filter_types,
    results_actor_filter_urls,
    results_actor_kind,
    results_column_count,
)
from games.results.table.access import (
    public_exclusion_notice,
    results_actor_for_request,
    results_me_participants as build_results_me_participants,
)
from games.results.table.tables import (
    load_results_placements_and_tasks,
    results_table_headers_context,
)
from games.results.table.presentation import paginate_results_rows, render_results_rows_partial
from games.results.table.cells import build_results_cells
from games.models import (
    Attempt,
    Team,
)
from games.models import GameResultsSnapshot
from games.results_snapshot import (
    get_live_results_payload,
    results_attempts_scope_game,
    snapshot_to_results_context,
)


from games.site.ui import NEW_UI_SECTIONS_PROJECT

logger = logging.getLogger('games.views.new_ui')

def _load_results_placements_and_tasks(game, task_group_number=None):
    """Compatibility wrapper for results consumers outside this module."""
    return load_results_placements_and_tasks(game, task_group_number=task_group_number)


def _results_table_headers_context(game, task_group_number=None):
    """Compatibility wrapper for the results table header context."""
    return results_table_headers_context(game, task_group_number=task_group_number)


def _results_column_count(task_groups, mode='general'):
    return results_column_count(task_groups, mode=mode)


def _results_rows_empty_context():
    return empty_results_rows_context()


def _load_game_results_data(game, mode):
    snap = GameResultsSnapshot.objects.filter(game=game, mode=mode).first()
    if snap and snap.payload and request.GET.get('actors') is None:
        return snapshot_to_results_context(game, snap.payload)
    # Live path: short-TTL cached snapshot-shaped payload (shared across progressive pages).
    return snapshot_to_results_context(game, get_live_results_payload(game, mode))


def _results_actor_for_request(request, game):
    """Compatibility wrapper for the viewer's results actor."""
    return results_actor_for_request(
        request,
        game,
        sections_project_id=NEW_UI_SECTIONS_PROJECT,
    )


def _public_exclusion_notice(request, game, task_group, *, surface):
    """Compatibility wrapper for the public results exclusion notice."""
    return public_exclusion_notice(
        request,
        game,
        task_group,
        surface=surface,
        sections_project_id=NEW_UI_SECTIONS_PROJECT,
    )


def _results_me_participants(request, play_mode):
    return build_results_me_participants(request, play_mode)


def _results_actor_filter_types(request):
    return results_actor_filter_types(request)


def _results_actor_filter_urls(request):
    return results_actor_filter_urls(request)


def _results_actor_kind(actor):
    return results_actor_kind(actor)


def _club_subscriber_user_ids(actors):
    return club_subscriber_user_ids(actors)


def _attach_results_club_badges(data):
    """Attach display-only Club badges without changing result/filter semantics."""
    return attach_results_club_badges(data)


def _new_results_compute_uncached(game, mode, task_group_number=None, alphabetty_sort='attempts', actor_types=None):
    variant = getattr(game, 'id', '')
    team_to_list_attempts_info = {}
    team_to_score = {}
    team_to_max_best_time = {}
    team_task_to_attempts_info = {}

    placements, task_group_to_tasks, tasks_flat, task_ids, task_group_headers = (
        _load_results_placements_and_tasks(game, task_group_number=task_group_number)
    )
    scoped_placement = next(
        (
            placement for placement in placements
            if str(placement.number) == str(task_group_number)
        ),
        None,
    ) if mode == 'general' and task_group_number is not None else None

    # A rebuilt task-level projection is the read model for a single ordinary
    # release.  Keep the existing canonical path for aggregate pages,
    # tournament windows, unsupported adapters, and stale/incomplete rows.
    projected_rows = None
    projection_fallback_durations = {}
    if mode == 'general' and task_group_number is not None:
        projection_link = scoped_placement
        if projection_link is not None:
            from games.daily.projection import load_task_result_projection_rows
            projected_rows = load_task_result_projection_rows(
                game,
                projection_link.task_group,
                task_ids,
                actor_types=actor_types,
            )
            if projected_rows is not None:
                from games.daily.projection import load_projection_fallback_durations
                projection_fallback_durations = load_projection_fallback_durations(
                    game, projection_link.task_group,
                )

    # General: SQL aggregates (same as live ladder snapshot). Tournament windows and
    # alphabetty letter-hint penalty still need the ORM bulk path. ``None`` is
    # the explicit projection fallback signal; an empty dict is valid data.
    bulk_game = results_attempts_scope_game(game, mode)
    if projected_rows is not None:
        bulk_rows = projected_rows
    elif mode == 'general':
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
                result_time = getattr(result_attempt, 'time', None)
                previous_time = team_to_max_best_time.get(participant)
                if result_time is not None and (previous_time is None or result_time > previous_time):
                    team_to_max_best_time[participant] = result_time

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
    from games.results.standings.leaderboard import (
        actor_key, canonical_leaderboard_durations, eligible_public_actors,
        individual_sports_key, score_rank,
    )
    solve_duration_seconds = {}
    solve_duration_fallback = set()
    result_times = {}
    scoped_group = scoped_placement
    published_at = None
    if mode == 'general' and task_group_number is not None:
        if scoped_group:
            from games.daily.page.section import publish_at_for
            published_at = publish_at_for(game, scoped_group.number)
            for actor in team_to_score:
                relevant = [
                    team_task_to_attempts_info.get((actor, task))
                    for task in tasks_flat
                    if task.task_group_id == scoped_group.task_group_id
                ]
                submitted = []
                for info in relevant:
                    # When authoritative start telemetry is unavailable, use
                    # the earliest persisted canonical attempt as the
                    # conservative publication fallback. A later retry must
                    # not make a pre-publication first play public.
                    for attempt in getattr(info, 'attempts', ()) if info else ():
                        attempt_time = getattr(attempt, 'time', None)
                        if attempt_time is not None:
                            submitted.append(attempt_time)
                if submitted:
                    result_times[actor] = min(submitted)
            solve_duration_seconds = canonical_leaderboard_durations(
                game=game, task_group=scoped_group.task_group, actors=team_to_score,
                fallback_actors=solve_duration_fallback,
                fallback_durations={
                    actor_key(actor): projection_fallback_durations[actor_key(actor)]
                    for actor in team_to_score
                    if actor_key(actor) in projection_fallback_durations
                },
            )
    eligible = set(eligible_public_actors(
        team_to_score, task_group=scoped_group.task_group if scoped_group else None,
        game=game if scoped_group else None, published_at=published_at,
        result_times=result_times,
    ))
    if actor_types is not None:
        eligible = {actor for actor in eligible if _results_actor_kind(actor) in actor_types}
    team_to_score = {actor: score for actor, score in team_to_score.items() if actor in eligible}
    solve_duration_seconds = {actor: value for actor, value in solve_duration_seconds.items() if actor in eligible}
    solve_duration_fallback &= set(solve_duration_seconds)
    team_to_attempts = {}
    if mode == 'tournament':
        # Keep the current tournament window and actor policy; use the actual
        # last counted submission timestamp as its sports tie-break.
        tournament_rows = [row[2] for row in sorted(teams_sorted, key=lambda row: (row[0], row[1], str(row[2]))) if row[2] in team_to_score]
        # `team_to_max_best_time` is the canonical last counted scoring time.
        sports_keys = {
            actor: (-team_to_score[actor], team_to_max_best_time[actor].timestamp() if team_to_max_best_time.get(actor) else float('inf'))
            for actor in tournament_rows
        }
        teams_sorted = tournament_rows
    else:
        attempt_counts = {}
        if variant in (ALPHABETTY_GAME_ID, 'censorly'):
            for actor in team_to_score:
                attempt_counts[actor] = sum(
                    int(info.get_n_attempts() or 0)
                    for task in tasks_flat
                    if task.task_group_id == getattr(scoped_group, 'task_group_id', None)
                    for info in [team_task_to_attempts_info.get((actor, task))]
                    if info is not None
                )
            team_to_attempts = attempt_counts
        sort_mode = alphabetty_sort
        def sport_key(actor):
            return individual_sports_key(
                score=team_to_score[actor],
                duration=solve_duration_seconds.get(actor),
                game_id=variant, attempts=attempt_counts.get(actor, 0),
                alphabetty_sort=sort_mode,
            )
        teams_sorted = sorted(team_to_score, key=lambda actor: (*sport_key(actor), str(actor)))
        sports_keys = {actor: sport_key(actor) for actor in teams_sorted}
    team_to_place = score_rank(teams_sorted, team_to_score)

    # Prepare per-cell metadata for templates in the results presentation layer.
    team_to_cells = build_results_cells(
        participants=teams_sorted,
        tasks=tasks_flat,
        attempts_by_participant=team_to_list_attempts_info,
        alphabetty=variant == ALPHABETTY_GAME_ID,
    )

    team_to_solve_duration = {}
    if mode == 'general' and task_group_number is not None:
        from games.results.share import format_elapsed_compact
        team_to_solve_duration = {
            actor: format_elapsed_compact(seconds)
            for actor, seconds in solve_duration_seconds.items() if actor in teams_sorted
        }

    return {
        'task_groups': task_group_headers,
        'task_group_to_tasks': task_group_to_tasks,
        'teams_sorted': teams_sorted,
        'team_to_list_attempts_info': team_to_list_attempts_info,
        'team_to_cells': team_to_cells,
        'team_to_score': team_to_score,
        'team_to_place': team_to_place,
        'team_to_max_best_time': team_to_max_best_time,
        'team_to_solve_duration': team_to_solve_duration,
        'team_to_solve_duration_fallback': solve_duration_fallback,
        'team_to_attempts': team_to_attempts,
    }


def _new_results_compute(game, mode, task_group_number=None, alphabetty_sort='attempts', actor_types=None):
    """Reuse the full result calculation while progressive pages are loading."""
    actor_key = 'all' if actor_types is None else ','.join(sorted(actor_types))
    cache_key = 'new_results:v2:{}:{}:{}:{}:{}'.format(
        game.id, mode, task_group_number or '-', alphabetty_sort, actor_key,
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    data = _new_results_compute_uncached(
        game, mode, task_group_number=task_group_number,
        alphabetty_sort=alphabetty_sort, actor_types=actor_types,
    )
    cache.set(cache_key, data, 10)
    return data


def _paginate_results_rows(request, data, per_page=50):
    """Compatibility wrapper for progressive results pagination."""
    return paginate_results_rows(request, data, per_page=per_page)


def _render_results_rows_partial(request, data, *, mode, results_variant='standard',
                                 team=None, me_personal=None,
                                 me_anon_participant=None,
                                 show_solve_duration=False,
                                 show_alphabetty_detail=False):
    """Compatibility wrapper for progressive results partial rendering."""
    return render_results_rows_partial(
        request,
        data,
        mode=mode,
        results_variant=results_variant,
        team=team,
        me_personal=me_personal,
        me_anon_participant=me_anon_participant,
        show_solve_duration=show_solve_duration,
        show_alphabetty_detail=show_alphabetty_detail,
    )



__all__ = [
    '_load_results_placements_and_tasks',
    '_results_table_headers_context',
    '_results_column_count',
    '_results_rows_empty_context',
    '_load_game_results_data',
    '_results_actor_for_request',
    '_public_exclusion_notice',
    '_results_me_participants',
    '_results_actor_filter_types',
    '_results_actor_filter_urls',
    '_results_actor_kind',
    '_club_subscriber_user_ids',
    '_attach_results_club_badges',
    '_new_results_compute_uncached',
    '_new_results_compute',
    '_paginate_results_rows',
    '_render_results_rows_partial',
]
