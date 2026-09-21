"""Bounded release-window builder for public section aggregate standings."""

from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace

from django.core.paginator import Paginator
from django.db.models import Prefetch, IntegerField
from django.db.models.functions import Cast

from games.leaderboard import eligible_release_actor_keys, sports_rank
from games.models import GameTaskGroup, PersonalResultsParticipant, Profile, Task, TaskGroup
from games.section_paths import section_play_path


WINDOW_CHOICES = (10, 20, 30)
PAGE_SIZE = 50


@dataclass(frozen=True)
class ReleaseColumn:
    link: object
    max_score: float | None
    label: str
    url: str


def _numbered_links(game):
    """Select published integer releases in the site's public number order."""
    from games.daily_section import current_number_for, schedule_for

    qs = GameTaskGroup.objects.filter(game=game).select_related('task_group').annotate(
        _release_number=Cast('number', IntegerField()),
    )
    schedule = schedule_for(game.id)
    current = current_number_for(game)
    if schedule is not None:
        if current is not None:
            qs = qs.filter(_release_number__lte=current)
        elif schedule.publish_start(game) is not None or not schedule.open_without_start:
            return qs.none()
    # These aggregate daily releases use integer numbers. Ignore non-numeric
    # legacy/share links: there is no canonical release order for them.
    return qs.filter(number__regex=r'^\d+$')


def _with_result_tasks(qs):
    return qs.prefetch_related(Prefetch(
        'task_group__tasks',
        queryset=Task.objects.visible().exclude(task_type='text_with_forms'),
        to_attr='result_tasks',
    ))


def _choose_window(game, *, limit, anchor):
    qs = _numbered_links(game)
    anchor_link = None
    cursor_ids = None
    if anchor and str(anchor).startswith('w:'):
        try:
            parsed_ids = [int(part) for part in str(anchor)[2:].split(',') if part]
            if parsed_ids and len(parsed_ids) <= 30 and all(value > 0 for value in parsed_ids):
                cursor_ids = parsed_ids
        except (TypeError, ValueError):
            cursor_ids = None
    if cursor_ids and len(cursor_ids) == limit:
        by_id = {link.pk: link for link in _with_result_tasks(qs.filter(pk__in=cursor_ids))}
        if len(by_id) == len(cursor_ids):
            current_window = [by_id[link_id] for link_id in cursor_ids]
            min_num = min(link._release_number for link in current_window)
            max_num = max(link._release_number for link in current_window)
            older_ids = list(
                qs.filter(_release_number__lt=min_num)
                .order_by('-_release_number', '-pk').values_list('pk', flat=True)[:limit]
            )
            newer_ids = list(
                qs.filter(_release_number__gt=max_num)
                .order_by('_release_number', 'pk').values_list('pk', flat=True)[:limit]
            )
            older_cursor = 'w:' + ','.join(str(value) for value in older_ids) if older_ids else None
            newer_cursor = 'w:' + ','.join(str(value) for value in reversed(newer_ids)) if newer_ids else None
            return current_window, older_cursor, newer_cursor

    # Changing limit from a window cursor keeps the old window's oldest release
    # as the anchor. Legacy numeric anchors continue to resolve as release IDs.
    if cursor_ids:
        anchor = str(cursor_ids[-1])
    if anchor and str(anchor).isdigit():
        anchor_id = int(anchor)
        if anchor_id <= 2147483647:
            anchor_link = qs.filter(pk=anchor_id).first()
    if anchor_link is None:
        anchor_link = qs.order_by('-_release_number', '-pk').first()
    if anchor_link is None:
        return [], None, None

    window_ids = list(
        qs.filter(_release_number__lte=anchor_link._release_number)
        .order_by('-_release_number', '-pk').values_list('pk', flat=True)[:limit]
    )
    if not window_ids:
        return [], None, None
    current_window = list(_with_result_tasks(qs.filter(pk__in=window_ids)).order_by('-_release_number', '-pk'))
    min_num = current_window[-1]._release_number
    max_num = current_window[0]._release_number

    older_ids = list(
        qs.filter(_release_number__lt=min_num).order_by('-_release_number', '-pk').values_list('pk', flat=True)[:limit]
    )
    newer_ids = list(
        qs.filter(_release_number__gt=max_num).order_by('_release_number', 'pk').values_list('pk', flat=True)[:limit]
    )
    older_cursor = 'w:' + ','.join(str(value) for value in older_ids) if older_ids else None
    newer_cursor = 'w:' + ','.join(str(value) for value in reversed(newer_ids)) if newer_ids else None
    return current_window, older_cursor, newer_cursor


def _max_score(tasks):
    total = 0.0
    for task in tasks:
        try:
            value = task.get_results_max_points()
        except Exception:
            return None
        if value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if value < 0:
            return None
        total += value
    return total


def _actor_key(actor):
    if getattr(actor, 'team_id', None) is not None and not hasattr(actor, 'anon_key'):
        return ('team', actor.team_id)
    if getattr(actor, 'user_id', None) is not None:
        return ('user', actor.user_id)
    if getattr(actor, 'anon_key', None):
        return ('anon', actor.anon_key)
    if hasattr(actor, 'is_hidden'):
        return ('team', actor.pk)
    return None


class _AggregatePage:
    def __init__(self, rows, number, count, per_page):
        self.object_list = rows
        self.number = number
        self.paginator = SimpleNamespace(
            count=count, per_page=per_page,
            num_pages=max(1, (count + per_page - 1) // per_page),
        )

    def has_next(self):
        return self.number < self.paginator.num_pages

    def has_previous(self):
        return self.number > 1

    def next_page_number(self):
        if not self.has_next():
            raise ValueError('That page has no next page')
        return self.number + 1

    def previous_page_number(self):
        if not self.has_previous():
            raise ValueError('That page has no previous page')
        return self.number - 1

    def start_index(self):
        return (self.number - 1) * self.paginator.per_page + 1 if self.object_list else 0

    def end_index(self):
        return (self.number - 1) * self.paginator.per_page + len(self.object_list)


def _projection_rank_page(game, group_ids, page_number):
    """Aggregate, rank and page persisted score rows without hydrating all actors."""
    from django.db import connection

    if not group_ids:
        return [], 0, 1
    q = connection.ops.quote_name
    projection = q('games_dailyresultprojection')
    team = q('games_team')
    profile = q('games_profile')
    hidden_anon = q('games_hiddenanonkey')
    author = q('games_taskgroup_authors')
    membership = q('games_profileteammembership')
    # IDs and all values are bound parameters. Table identifiers are fixed app
    # schema names quoted through the active database backend.
    placeholders = ', '.join(['%s'] * len(group_ids))
    eligibility = f'''p.game_id = %s AND p.task_group_id IN ({placeholders})
      AND p.is_prepublication = %s
      AND (p.team_id IS NULL OR EXISTS (
          SELECT 1 FROM {team} t WHERE t.name = p.team_id AND t.is_hidden = %s))
      AND (p.user_id IS NULL OR NOT EXISTS (
          SELECT 1 FROM {profile} pr WHERE pr.user_id = p.user_id AND pr.is_hidden = %s))
      AND (p.anon_key IS NULL OR NOT EXISTS (
          SELECT 1 FROM {hidden_anon} ha WHERE ha.anon_key = p.anon_key))
      AND NOT (p.actor_type = %s AND EXISTS (
          SELECT 1 FROM {author} a WHERE a.taskgroup_id = p.task_group_id AND a.profile_id = p.user_id))
      AND NOT (p.actor_type = %s AND (
          EXISTS (SELECT 1 FROM {author} a JOIN {membership} m ON m.profile_id = a.profile_id
                  WHERE a.taskgroup_id = p.task_group_id AND m.team_id = p.team_id)
          OR EXISTS (SELECT 1 FROM {author} a JOIN {profile} pr ON pr.user_id = a.profile_id
                     WHERE a.taskgroup_id = p.task_group_id AND pr.team_on_id = p.team_id)))'''
    base_params = [game.pk, *group_ids, False, True, True, 'user', 'team']
    with connection.cursor() as cursor:
        cursor.execute(
            f'''WITH actor_totals AS (
                    SELECT p.actor_type, p.actor_key, p.team_id, p.user_id, p.anon_key,
                           SUM(p.score) AS window_score, COUNT(p.id) AS played_count
                    FROM {projection} p WHERE {eligibility}
                    GROUP BY p.actor_type, p.actor_key, p.team_id, p.user_id, p.anon_key
                ) SELECT COUNT(*) FROM actor_totals''',
            base_params,
        )
        total = int(cursor.fetchone()[0])
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page_number = max(1, min(int(page_number or 1), pages))
        cursor.execute(
            f'''WITH actor_totals AS (
                    SELECT p.actor_type, p.actor_key, p.team_id, p.user_id, p.anon_key,
                           SUM(p.score) AS window_score, COUNT(p.id) AS played_count
                    FROM {projection} p WHERE {eligibility}
                    GROUP BY p.actor_type, p.actor_key, p.team_id, p.user_id, p.anon_key
                ), ranked AS (
                    SELECT actor_type, actor_key, team_id, user_id, anon_key,
                           window_score, played_count,
                           RANK() OVER (ORDER BY window_score DESC) AS place
                    FROM actor_totals
                ) SELECT actor_type, actor_key, team_id, user_id, anon_key,
                         window_score, played_count, place
                  FROM ranked ORDER BY window_score DESC, actor_type, actor_key
                  LIMIT %s OFFSET %s''',
            [*base_params, PAGE_SIZE, (page_number - 1) * PAGE_SIZE],
        )
        rows = [dict(zip((c[0] for c in cursor.description), row)) for row in cursor.fetchall()]
    return rows, total, page_number


def _projection_page_cells(game, group_ids, page_rows):
    """Fetch only the page's eligible release cells, applying the same row policy."""
    from django.db import connection

    if not group_ids or not page_rows:
        return defaultdict(dict)
    q = connection.ops.quote_name
    projection, team = q('games_dailyresultprojection'), q('games_team')
    profile, hidden_anon = q('games_profile'), q('games_hiddenanonkey')
    author, membership = q('games_taskgroup_authors'), q('games_profileteammembership')
    release_args = ', '.join(['%s'] * len(group_ids))
    identities = []
    identity_args = []
    for row in page_rows:
        identities.append('(p.actor_type = %s AND p.actor_key = %s)')
        identity_args.extend((row['actor_type'], row['actor_key']))
    eligible = f'''p.game_id = %s AND p.task_group_id IN ({release_args}) AND p.is_prepublication = %s
      AND (p.team_id IS NULL OR EXISTS (SELECT 1 FROM {team} t WHERE t.name=p.team_id AND t.is_hidden=%s))
      AND (p.user_id IS NULL OR NOT EXISTS (SELECT 1 FROM {profile} pr WHERE pr.user_id=p.user_id AND pr.is_hidden=%s))
      AND (p.anon_key IS NULL OR NOT EXISTS (SELECT 1 FROM {hidden_anon} ha WHERE ha.anon_key=p.anon_key))
      AND NOT (p.actor_type=%s AND EXISTS (SELECT 1 FROM {author} a WHERE a.taskgroup_id=p.task_group_id AND a.profile_id=p.user_id))
      AND NOT (p.actor_type=%s AND (
        EXISTS (SELECT 1 FROM {author} a JOIN {membership} m ON m.profile_id=a.profile_id WHERE a.taskgroup_id=p.task_group_id AND m.team_id=p.team_id)
        OR EXISTS (SELECT 1 FROM {author} a JOIN {profile} pr ON pr.user_id=a.profile_id WHERE a.taskgroup_id=p.task_group_id AND pr.team_on_id=p.team_id)))'''
    params = [game.pk, *group_ids, False, True, True, 'user', 'team', *identity_args]
    with connection.cursor() as cursor:
        cursor.execute(
            f'''SELECT p.actor_type, p.actor_key, p.task_group_id, p.score
                FROM {projection} p WHERE {eligible} AND ({' OR '.join(identities)})''',
            params,
        )
        cells = defaultdict(dict)
        for actor_type, actor_key, group_id, score in cursor.fetchall():
            cells[(actor_type, actor_key)][group_id] = score
    return cells


def _build_legacy_aggregate_page(request, game, *, window_context=None):
    """Build one bounded release window and one backend-paginated actor page.

    The existing score aggregators remain authoritative. Their input is limited
    to this page's <=30 releases, and no attempt/subtask cells reach the template.
    """
    from games.results_sql_aggregate import (
        get_sql_aggregated_game_actor_rows,
        tasks_need_orm_results_aggregate,
    )
    from games.daily_section import publish_at_for
    from games.results_snapshot import results_attempts_scope_game

    if window_context is not None:
        limit, window, older, newer, columns = window_context
    else:
        try:
            requested = int(request.GET.get('limit', '10'))
        except (TypeError, ValueError):
            requested = 10
        limit = requested if requested in WINDOW_CHOICES else 10
        window, older, newer = _choose_window(game, limit=limit, anchor=request.GET.get('anchor'))
        columns = []
        for link in window:
            maximum = _max_score(link.task_group.result_tasks)
            columns.append(ReleaseColumn(
                link=link, max_score=maximum,
                label=link.number,
                url=section_play_path(game.id, link.number) + 'results/',
            ))

    task_by_id = {task.id: (column, task) for column in columns for task in column.link.task_group.result_tasks}
    task_ids = list(task_by_id)
    totals = defaultdict(float)
    played = defaultdict(set)
    cells = defaultdict(dict)
    actors = {}

    if task_ids:
        scoped_game = results_attempts_scope_game(game, 'general')
        tasks = [item[1] for item in task_by_id.values()]
        # Preserve the existing ORM path for Word Salad and the SQL path for
        # ordinary tasks. Both compute the canonical result, excluding replay.
        if tasks_need_orm_results_aggregate(tasks):
            result_rows = __import__('games.models', fromlist=['Attempt']).Attempt.manager.get_bulk_game_actor_rows(
                task_ids, mode='general', game=scoped_game,
            )
        else:
            result_rows = get_sql_aggregated_game_actor_rows(task_ids, game=scoped_game)

        by_release = defaultdict(set)
        tentative_scores = defaultdict(dict)
        result_times = defaultdict(dict)
        for task_id, rows in result_rows.items():
            column, task = task_by_id[task_id]
            for actor, info in rows:
                key = _actor_key(actor)
                if key is None or not (info.attempts or info.hint_attempts):
                    continue
                actors.setdefault(key, actor)
                by_release[column.link.task_group_id].add(key)
                tentative_scores[(column.link.task_group_id, key)][task_id] = float(info.get_result_points() or 0)
                attempts = [attempt for attempt in (info.attempts or ()) if getattr(attempt, 'time', None)]
                if attempts:
                    first_completion = min(attempt.time for attempt in attempts)
                    prev = result_times[column.link.task_group_id].get(key)
                    if prev is None or first_completion < prev:
                        result_times[column.link.task_group_id][key] = first_completion

        # Bulk task-aware exclusions per release. Hidden actors, authors,
        # team rosters and first-start telemetry are loaded in bounded queries.
        published_by_release = {}
        for column in columns:
            published_by_release[column.link.task_group_id] = publish_at_for(game, column.link.number)
        eligible_by_release = eligible_release_actor_keys(
            {release_id: {actors[key] for key in keys} for release_id, keys in by_release.items()},
            game=game,
            published_at_by_release=published_by_release,
            result_times_by_release={
                release_id: {actors[key]: stamp for key, stamp in rows.items()}
                for release_id, rows in result_times.items()
            },
        )
        for column in columns:
            group = column.link.task_group
            candidate_keys = by_release.get(group.pk, set())
            eligible = eligible_by_release.get(group.pk, set())
            for key in candidate_keys & eligible:
                release_score = sum(tentative_scores.get((group.pk, key), {}).values())
                cells[key][column.link.pk] = release_score
                totals[key] += release_score
                played[key].add(column.link.pk)

    # Shared sports rank is score only; actor key only orders ties for display.
    ordered_keys = sorted(actors, key=lambda key: (-totals[key], str(key)))
    user_ids = {key[1] for key in actors if key[0] == 'user'}
    profile_names = {
        user_id: (first_name, last_name)
        for user_id, first_name, last_name in Profile.objects.filter(
            user_id__in=user_ids,
        ).values_list('user_id', 'first_name', 'last_name')
    } if user_ids else {}
    for key in ordered_keys:
        actor = actors[key]
        if key[0] == 'user' and hasattr(actor, '_display_name_override'):
            first_name, last_name = profile_names.get(key[1], ('', ''))
            label = '{} {}'.format(first_name or '', last_name or '').strip()
            if not label and getattr(actor, '_user', None) is not None:
                label = (actor._user.get_full_name() or actor._user.get_username()).strip()
            actor._display_name_override = label
    keys = {key: (-totals[key],) for key in ordered_keys}
    ranks = sports_rank(ordered_keys, keys)
    paginator = Paginator(ordered_keys, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    rows = []
    window_max = sum(c.max_score for c in columns if c.max_score is not None)
    for key in page_obj.object_list:
        rows.append({
            'actor': actors[key], 'place': ranks[key], 'score': totals[key],
            'max_score': window_max, 'played': len(played[key]),
            'cells': cells[key],
        })

    return {
        'aggregate_leaderboard': True,
        'aggregate_columns': columns,
        'aggregate_rows': rows,
        'aggregate_window_max': window_max,
        'aggregate_has_unknown_max': any(c.max_score is None for c in columns),
        'aggregate_limit': limit,
        'aggregate_page': page_obj,
        'aggregate_older_anchor': older,
        'aggregate_newer_anchor': newer,
        'aggregate_window_anchor': 'w:' + ','.join(str(link.pk) for link in window) if window else None,
        'aggregate_first_number': window[-1].number if window else None,
        'aggregate_last_number': window[0].number if window else None,
        'team_to_score': {row['actor']: row['score'] for row in rows},
        'team_to_place': {row['actor']: row['place'] for row in rows},
        'teams_sorted': [row['actor'] for row in rows],
    }


def build_aggregate_page(request, game):
    """Use SQL ranking only when every displayed release has a rebuilt projection.

    During rollout, an incomplete window uses the exact existing calculator as
    a whole-page fallback; it never mixes projected totals with missing rows.
    """
    import logging
    from games.models import (
        DailyResultProjection, DailyResultProjectionState,
        Team, User,
    )
    from games.daily_result_projection import scorer_adapter_version

    logger = logging.getLogger(__name__)
    try:
        requested = int(request.GET.get('limit', '10'))
    except (TypeError, ValueError):
        requested = 10
    limit = requested if requested in WINDOW_CHOICES else 10
    window, older, newer = _choose_window(game, limit=limit, anchor=request.GET.get('anchor'))
    columns = []
    for link in window:
        maximum = _max_score(link.task_group.result_tasks)
        columns.append(ReleaseColumn(
            link=link, max_score=maximum, label=link.number,
            url=section_play_path(game.id, link.number) + 'results/',
        ))

    group_ids = [link.task_group_id for link in window]
    states = {
        state.task_group_id: state.adapter_version
        for state in DailyResultProjectionState.objects.filter(game=game, task_group_id__in=group_ids)
    } if group_ids else {}
    stale_groups = [
        link.task_group_id for link in window
        if states.get(link.task_group_id) != scorer_adapter_version(game, link.task_group)
    ]
    covered = len(set(group_ids)) - len(set(stale_groups))
    if stale_groups:
        logger.warning(
            'daily_result_projection_incomplete game=%s covered=%s expected=%s; using canonical legacy builder',
            game.pk, covered, len(set(group_ids)),
        )
        return _build_legacy_aggregate_page(
            request, game, window_context=(limit, window, older, newer, columns),
        )

    try:
        requested_page = int(request.GET.get('page', '1'))
    except (TypeError, ValueError):
        requested_page = 1
    page_values, total_count, current_page = _projection_rank_page(game, group_ids, requested_page)
    page_obj = _AggregatePage(page_values, current_page, total_count, PAGE_SIZE)
    team_ids = {r['team_id'] for r in page_values if r['team_id']}
    user_ids = {r['user_id'] for r in page_values if r['user_id']}
    teams = {t.pk: t for t in Team.objects.filter(pk__in=team_ids)}
    users = {u.pk: u for u in User.objects.filter(pk__in=user_ids).select_related('profile')}
    actor_by_identity = {}
    for row in page_values:
        if row['actor_type'] == DailyResultProjection.ACTOR_TEAM:
            actor_by_identity[(row['actor_type'], row['actor_key'])] = teams.get(row['team_id'])
        elif row['actor_type'] == DailyResultProjection.ACTOR_USER:
            user = users.get(row['user_id'])
            actor_by_identity[(row['actor_type'], row['actor_key'])] = PersonalResultsParticipant(user=user) if user else None
        else:
            actor_by_identity[(row['actor_type'], row['actor_key'])] = PersonalResultsParticipant(anon_key=row['anon_key'])
    cells = _projection_page_cells(game, group_ids, page_values)

    window_max = sum(c.max_score for c in columns if c.max_score is not None)
    rows = []
    for result in page_values:
        identity = (result['actor_type'], result['actor_key'])
        actor = actor_by_identity.get(identity)
        if actor is None:
            continue
        if result['actor_type'] == DailyResultProjection.ACTOR_USER:
            profile = getattr(actor._user, 'profile', None)
            if profile:
                actor._display_name_override = '{} {}'.format(profile.first_name or '', profile.last_name or '').strip()
        rows.append({
            'actor': actor, 'place': result['place'], 'score': result['window_score'],
            'max_score': window_max, 'played': result['played_count'],
            'cells': {link.pk: cells[identity][link.task_group_id] for link in window if link.task_group_id in cells[identity]},
        })
    return {
        'aggregate_leaderboard': True, 'aggregate_columns': columns,
        'aggregate_rows': rows, 'aggregate_window_max': window_max,
        'aggregate_has_unknown_max': any(c.max_score is None for c in columns),
        'aggregate_limit': limit, 'aggregate_page': page_obj,
        'aggregate_older_anchor': older,
        'aggregate_newer_anchor': newer,
        'aggregate_window_anchor': 'w:' + ','.join(str(link.pk) for link in window) if window else None,
        'aggregate_first_number': window[-1].number if window else None,
        'aggregate_last_number': window[0].number if window else None,
        'team_to_score': {row['actor']: row['score'] for row in rows},
        'team_to_place': {row['actor']: row['place'] for row in rows},
        'teams_sorted': [row['actor'] for row in rows],
    }
