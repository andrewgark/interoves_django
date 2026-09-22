"""Shared public leaderboard eligibility and sporting-place semantics."""

from django.db.models import Q
from games.share_result import format_elapsed_compact

from games.models import DailySolveTiming, HiddenAnonKey, Profile, TaskGroup, Team


def actor_key(actor):
    if isinstance(actor, Team):
        return ('team', actor.pk)
    user_id = getattr(actor, 'user_id', None)
    if user_id is not None:
        return ('user', user_id)
    anon_key = getattr(actor, 'anon_key', None)
    if anon_key:
        return ('anon', anon_key)
    return None


def eligible_public_actors(actors, *, task_group=None, game=None, published_at=None, result_times=None):
    """Filter actor objects in bulk; actor identity and stored results stay intact."""
    actors = list(actors)
    team_ids = {a.pk for a in actors if isinstance(a, Team)}
    user_ids = {a.user_id for a in actors if getattr(a, 'user_id', None) is not None}
    anon_keys = {a.anon_key for a in actors if getattr(a, 'anon_key', None)}
    hidden_teams = set(Team.objects.filter(pk__in=team_ids, is_hidden=True).values_list('pk', flat=True))
    hidden_users = set(Profile.objects.filter(user_id__in=user_ids, is_hidden=True).values_list('user_id', flat=True))
    hidden_anons = set(HiddenAnonKey.objects.filter(anon_key__in=anon_keys).values_list('anon_key', flat=True))
    authors = set()
    if task_group is not None:
        authors = set(task_group.authors.values_list('user_id', flat=True))
    team_authors = set()
    if authors and team_ids:
        team_authors = set(Profile.objects.filter(
            user_id__in=authors,
        ).filter(
            Q(team_memberships__team_id__in=team_ids) | Q(team_on_id__in=team_ids),
        ).values_list('team_memberships__team_id', 'team_on_id').distinct())
        team_authors = {
            team_id for membership_team_id, primary_team_id in team_authors
            for team_id in (membership_team_id, primary_team_id)
            if team_id in team_ids
        }
    prepublication = set()
    if task_group is not None and game is not None and published_at is not None:
        timings = DailySolveTiming.objects.filter(
            game=game, task_group=task_group, replay_slot__isnull=True,
        ).filter(Q(team_id__in=team_ids) | Q(user_id__in=user_ids) | Q(anon_key__in=anon_keys)).only(
            'team_id', 'user_id', 'anon_key', 'created_at',
        )
        for row in timings:
            if row.created_at and row.created_at < published_at:
                key = ('team', row.team_id) if row.team_id else (
                    ('user', row.user_id) if row.user_id else ('anon', row.anon_key)
                )
                prepublication.add(key)
        for actor, submitted_at in (result_times or {}).items():
            key = actor_key(actor)
            # If first-start telemetry is absent, the canonical result's first
            # recorded submission is the conservative fallback. Unknown stays public.
            if key and key not in prepublication and submitted_at and submitted_at < published_at:
                prepublication.add(key)
    return [a for a in actors if not (
        (isinstance(a, Team) and a.pk in hidden_teams)
        or (isinstance(a, Team) and a.pk in team_authors)
        or (getattr(a, 'user_id', None) in hidden_users)
        or (getattr(a, 'user_id', None) in authors)
        or (getattr(a, 'anon_key', None) in hidden_anons)
        or (actor_key(a) in prepublication)
    )]


def eligible_release_actor_keys(release_actors, *, game, published_at_by_release, result_times_by_release):
    """Bulk task-aware eligibility for aggregate windows (bounded query count)."""
    from collections import defaultdict

    release_actors = {release_id: set(actors) for release_id, actors in release_actors.items()}
    all_actors = set().union(*release_actors.values()) if release_actors else set()
    globally_eligible = set(eligible_public_actors(all_actors))
    eligible_by_key = {actor_key(actor): actor for actor in globally_eligible}
    release_ids = list(release_actors)
    authors_by_release = defaultdict(set)
    if release_ids:
        for row in TaskGroup.authors.through.objects.filter(
            taskgroup_id__in=release_ids,
        ).values_list('taskgroup_id', 'profile_id'):
            authors_by_release[row[0]].add(row[1])

    team_ids = {a.pk for a in globally_eligible if isinstance(a, Team)}
    author_ids = set().union(*authors_by_release.values()) if authors_by_release else set()
    authored_teams_by_profile = defaultdict(set)
    if author_ids and team_ids:
        for profile_id, membership_team_id, primary_team_id in Profile.objects.filter(
            user_id__in=author_ids,
        ).filter(
            Q(team_memberships__team_id__in=team_ids) | Q(team_on_id__in=team_ids),
        ).values_list('user_id', 'team_memberships__team_id', 'team_on_id').distinct():
            if membership_team_id:
                authored_teams_by_profile[profile_id].add(membership_team_id)
            if primary_team_id:
                authored_teams_by_profile[profile_id].add(primary_team_id)

    users = {a.user_id for a in globally_eligible if getattr(a, 'user_id', None)}
    anons = {a.anon_key for a in globally_eligible if getattr(a, 'anon_key', None)}
    starts = {}
    if release_ids and (team_ids or users or anons):
        qs = DailySolveTiming.objects.filter(
            game=game, task_group_id__in=release_ids, replay_slot__isnull=True,
        ).filter(Q(team_id__in=team_ids) | Q(user_id__in=users) | Q(anon_key__in=anons)).values_list(
            'task_group_id', 'team_id', 'user_id', 'anon_key', 'created_at',
        )
        for release_id, team_id, user_id, anon_key, started_at in qs:
            actor = ('team', team_id) if team_id else (
                ('user', user_id) if user_id else ('anon', anon_key)
            )
            starts[(release_id, actor)] = started_at

    output = {}
    for release_id, actors in release_actors.items():
        authors = authors_by_release.get(release_id, set())
        publication = published_at_by_release.get(release_id)
        result_times = result_times_by_release.get(release_id, {})
        visible = set()
        for actor in actors & globally_eligible:
            key = actor_key(actor)
            if key is None:
                continue
            if key[0] == 'user' and key[1] in authors:
                continue
            if key[0] == 'team' and any(
                team_id in authored_teams_by_profile.get(author_id, ())
                for author_id in authors
            ):
                continue
            if publication is not None:
                started_at = starts.get((release_id, key))
                if started_at is not None:
                    if started_at < publication:
                        continue
                else:
                    result_at = result_times.get(actor)
                    if result_at is not None and result_at < publication:
                        continue
            visible.add(key)
        output[release_id] = visible
    return output


def canonical_leaderboard_durations(*, game, task_group, actors):
    """Known active duration seconds for first personal/anon play; missing = NULL.

    This intentionally never derives a duration from Attempt timestamps.
    """
    actors = list(actors)
    users = {getattr(a, 'user_id', None) for a in actors} - {None}
    anons = {getattr(a, 'anon_key', None) for a in actors} - {None}
    teams = {a.pk for a in actors if isinstance(a, Team)}
    if not users and not anons and not teams:
        return {}
    rows = DailySolveTiming.objects.filter(
        game=game, task_group=task_group, replay_slot__isnull=True,
    ).filter(Q(team_id__in=teams) | Q(user_id__in=users) | Q(anon_key__in=anons)).only(
        'team_id', 'user_id', 'anon_key', 'timing_version', 'status', 'frozen_ms', 'accumulated_ms',
    )
    from games.daily_timing import canonical_elapsed_seconds
    result = {}
    by_key = {
        (('team', row.team_id) if row.team_id else (
            ('user', row.user_id) if row.user_id else ('anon', row.anon_key)
        )): row
        for row in rows
    }
    for actor in actors:
        key = actor_key(actor)
        row = by_key.get(key)
        if row and int(row.timing_version or 0) >= DailySolveTiming.TIMING_VERSION_ACTIVE:
            result[actor] = canonical_elapsed_seconds(
                game=game, task_group=task_group, timing_row=row,
            )
    return result


def apply_release_policy(data, *, game, task_group, published_at=None, result_times=None):
    """Apply shared eligibility, canonical time ordering, labels and sports places."""
    actors = list(data.get('teams_sorted') or [])
    eligible = set(eligible_public_actors(
        actors, task_group=task_group, game=game,
        published_at=published_at, result_times=result_times,
    ))
    actors = [actor for actor in actors if actor in eligible]
    durations = canonical_leaderboard_durations(game=game, task_group=task_group, actors=actors)
    scores = data.get('team_to_score') or {}
    sports_keys = {
        actor: (-scores.get(actor, 0), durations.get(actor) is None, durations.get(actor, 0))
        for actor in actors
    }
    actors.sort(key=lambda actor: (*sports_keys[actor], str(actor)))
    data['teams_sorted'] = actors
    data['team_to_place'] = score_rank(actors, scores)
    data['team_to_solve_duration'] = {
        actor: format_elapsed_compact(seconds)
        for actor, seconds in durations.items()
    }
    for key in ('team_to_score', 'team_to_cells', 'team_to_list_attempts_info', 'team_to_max_best_time'):
        mapping = data.get(key)
        if isinstance(mapping, dict):
            data[key] = {actor: value for actor, value in mapping.items() if actor in eligible}
    return data


def sports_rank(ordered_actors, sports_keys):
    """Map actor to competition rank by sporting criteria, ignoring display fallback."""
    places = {}
    previous = object()
    place = 0
    for index, actor in enumerate(ordered_actors, 1):
        key = sports_keys[actor]
        if key != previous:
            place = index
            previous = key
        places[actor] = place
    return places


def score_rank(ordered_actors, scores):
    """Rank an already sorted result list by score only.

    Secondary criteria (time, attempts) may determine row order, but never
    split equal-score places.
    """
    places = {}
    previous_score = object()
    place = 0
    for index, actor in enumerate(ordered_actors, 1):
        score = scores.get(actor, 0)
        if score != previous_score:
            place = index
            previous_score = score
        places[actor] = place
    return places


def individual_sports_key(*, score, duration, game_id='', attempts=0, alphabetty_sort='attempts'):
    """Official individual ranking tuple; duration is seconds or NULL."""
    score_key = -score
    duration_key = (duration is None, duration if duration is not None else 0)
    if str(game_id) == 'alphabetty':
        if alphabetty_sort == 'time':
            return (score_key, *duration_key, attempts)
        return (score_key, attempts, *duration_key)
    return (score_key, *duration_key)
