"""Durable month-level aggregation for scheduled-game leaderboards."""

import hashlib
import json
import logging
from django.db import DatabaseError, IntegrityError, connection, transaction

from games.models import (
    DailyMonthlyResultProjection,
    DailyMonthlyResultProjectionState,
    DailyResultProjection,
    DailyResultProjectionState,
    Profile,
    ProfileTeamMembership,
    TaskGroup,
)
from games.daily.projection import projection_state_is_valid

logger = logging.getLogger('application')


def _source_signature(game, month, group_ids):
    """Fingerprint the inputs that affect persisted monthly score totals."""
    with transaction.atomic():
        return _source_signature_in_snapshot(game, month, group_ids)


def _source_signature_in_snapshot(game, month, group_ids):
    """Read every signature component from one consistent DB snapshot."""
    group_ids = sorted(set(group_ids))
    states = {
        row.task_group_id: row
        for row in DailyResultProjectionState.objects.filter(
            game=game, task_group_id__in=group_ids,
        )
    }
    if len(states) != len(group_ids):
        return None
    state_values = []
    for group_id in group_ids:
        row = states[group_id]
        if not projection_state_is_valid(row, game):
            return None
        state_values.append((
            group_id, row.source_revision, row.adapter_version,
            row.completed_at.isoformat() if row.completed_at else None,
        ))

    through = TaskGroup.authors.through
    author_pairs = sorted(through.objects.filter(
        taskgroup_id__in=group_ids,
    ).values_list('taskgroup_id', 'profile_id'))
    profile_ids = sorted({profile_id for _group_id, profile_id in author_pairs})
    team_assignments = []
    if profile_ids:
        memberships = ProfileTeamMembership.objects.filter(
            profile_id__in=profile_ids,
        ).values_list('profile_id', 'team_id')
        team_assignments.extend([
            ('member', profile_id, team_id)
            for profile_id, team_id in memberships
        ])
        primary_teams = Profile.objects.filter(
            user_id__in=profile_ids,
        ).values_list('user_id', 'team_on_id')
        team_assignments.extend([
            ('primary', profile_id, team_id)
            for profile_id, team_id in primary_teams
        ])

    payload = {
        'game': game.pk,
        'month': month.isoformat(),
        'groups': group_ids,
        'states': state_values,
        'authors': author_pairs,
        'author_teams': sorted(team_assignments),
    }
    return hashlib.sha256(json.dumps(
        payload, separators=(',', ':'), sort_keys=True,
    ).encode('utf-8')).hexdigest()


def _aggregate_rows(game, group_ids):
    """Aggregate eligible published score cells, excluding release authors."""
    q = connection.ops.quote_name
    projection = q(DailyResultProjection._meta.db_table)
    authors = q(TaskGroup.authors.through._meta.db_table)
    memberships = q(ProfileTeamMembership._meta.db_table)
    profiles = q(Profile._meta.db_table)
    placeholders = ', '.join(['%s'] * len(group_ids))
    sql = f'''SELECT p.actor_type, p.actor_key, p.team_id, p.user_id, p.anon_key,
                     SUM(p.score), SUM(p.attempts_count), COUNT(p.id)
                FROM {projection} p
               WHERE p.game_id = %s AND p.task_group_id IN ({placeholders})
                 AND p.is_prepublication = %s
                 AND NOT (p.actor_type = %s AND EXISTS (
                     SELECT 1 FROM {authors} a
                      WHERE a.taskgroup_id = p.task_group_id AND a.profile_id = p.user_id))
                 AND NOT (p.actor_type = %s AND (
                     EXISTS (SELECT 1 FROM {authors} a JOIN {memberships} m
                               ON m.profile_id = a.profile_id
                              WHERE a.taskgroup_id = p.task_group_id AND m.team_id = p.team_id)
                     OR EXISTS (SELECT 1 FROM {authors} a JOIN {profiles} pr
                                  ON pr.user_id = a.profile_id
                                 WHERE a.taskgroup_id = p.task_group_id AND pr.team_on_id = p.team_id)))
            GROUP BY p.actor_type, p.actor_key, p.team_id, p.user_id, p.anon_key'''
    with connection.cursor() as cursor:
        cursor.execute(sql, [game.pk, *group_ids, False, 'user', 'team'])
        return cursor.fetchall()


def _get_or_create_state(game, month):
    try:
        with transaction.atomic():
            return DailyMonthlyResultProjectionState.objects.get_or_create(
                game=game, month=month,
            )[0]
    except IntegrityError:
        return DailyMonthlyResultProjectionState.objects.get(game=game, month=month)


def ensure_monthly_projection(game, month, group_ids):
    try:
        return _ensure_monthly_projection(game, month, group_ids)
    except DatabaseError:
        logger.exception(
            'monthly_result_projection_build_failed game=%s month=%s; using canonical aggregate query',
            game.pk, month,
        )
        return False


def _ensure_monthly_projection(game, month, group_ids):
    """Build atomically when missing or stale; return whether it is ready."""
    if not group_ids:
        return False
    group_ids = sorted(set(group_ids))
    state = _get_or_create_state(game, month)

    for _attempt in range(2):
        signature = _source_signature(game, month, group_ids)
        if signature is None:
            return False
        if state.source_signature == signature:
            return True

        rows = _aggregate_rows(game, group_ids)
        if _source_signature(game, month, group_ids) != signature:
            continue

        with transaction.atomic():
            state = DailyMonthlyResultProjectionState.objects.select_for_update().get(
                game=game, month=month,
            )
            current_signature = _source_signature(game, month, group_ids)
            if current_signature != signature:
                continue
            if state.source_signature == signature:
                return True
            DailyMonthlyResultProjection.objects.filter(game=game, month=month).delete()
            DailyMonthlyResultProjection.objects.bulk_create([
                DailyMonthlyResultProjection(
                    game=game,
                    month=month,
                    actor_type=row[0],
                    actor_key=row[1],
                    team_id=row[2],
                    user_id=row[3],
                    anon_key=row[4],
                    score=row[5],
                    attempts_count=row[6],
                    played_count=row[7],
                )
                for row in rows
            ], batch_size=1000)
            state.source_signature = signature
            state.save(update_fields=['source_signature', 'updated_at'])
        logger.info(
            'monthly_result_projection_rebuilt game=%s month=%s actors=%s groups=%s',
            game.pk, month, len(rows), len(group_ids),
        )
        return _source_signature(game, month, group_ids) == signature
    return False
