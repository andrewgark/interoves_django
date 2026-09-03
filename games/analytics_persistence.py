import re
from dataclasses import dataclass

from django.db import IntegrityError, connections, transaction


@dataclass(frozen=True)
class AnalyticsUniqueSpec:
    model_name: str
    actor_field: str
    columns: tuple
    index_name: str


ANALYTICS_UNIQUE_SPECS = (
    AnalyticsUniqueSpec(
        'PlayerStartedGame', 'user_id', ('user_id', 'game_instance_id'),
        'uniq_started_game_user_instance',
    ),
    AnalyticsUniqueSpec(
        'PlayerStartedGame', 'anon_key', ('anon_key', 'game_instance_id'),
        'uniq_started_game_anon_instance',
    ),
    AnalyticsUniqueSpec(
        'PlayerStartedGame', 'team_id', ('team_id', 'game_instance_id'),
        'uniq_started_game_team_instance',
    ),
    AnalyticsUniqueSpec(
        'PlayerCompletedGame', 'user_id', ('user_id', 'game_instance_id'),
        'uniq_completed_game_user_instance',
    ),
    AnalyticsUniqueSpec(
        'PlayerCompletedGame', 'anon_key', ('anon_key', 'game_instance_id'),
        'uniq_completed_game_anon_instance',
    ),
    AnalyticsUniqueSpec(
        'PlayerCompletedGame', 'team_id', ('team_id', 'game_instance_id'),
        'uniq_completed_game_team_instance',
    ),
    AnalyticsUniqueSpec(
        'PlayerAnalyticsState', 'user_id', ('user_id',),
        'uniq_player_analytics_state_user',
    ),
    AnalyticsUniqueSpec(
        'PlayerAnalyticsState', 'anon_key', ('anon_key',),
        'uniq_player_analytics_state_anon',
    ),
    AnalyticsUniqueSpec(
        'PlayerAnalyticsState', 'team_id', ('team_id',),
        'uniq_player_analytics_state_team',
    ),
)

ANALYTICS_UNIQUE_SPEC_BY_NAME = {
    spec.index_name: spec for spec in ANALYTICS_UNIQUE_SPECS
}


class AnalyticsRowInvariantError(RuntimeError):
    """The exact analytics identity key resolves to more than one row."""


def _lookup_value(lookup, field):
    if field in lookup:
        value = lookup[field]
    else:
        relation_field = field[:-3] if field.endswith('_id') else None
        if relation_field is None or relation_field not in lookup:
            return None
        value = lookup[relation_field]
    if hasattr(value, 'pk'):
        return value.pk
    return value


def analytics_unique_spec(model, lookup):
    model_name = model._meta.object_name
    populated = []
    for actor_field in ('user_id', 'anon_key', 'team_id'):
        if _lookup_value(lookup, actor_field) is not None:
            populated.append(actor_field)
    if len(populated) != 1:
        raise AnalyticsRowInvariantError(
            '{} create lookup must contain exactly one analytics identity'.format(
                model_name,
            )
        )
    actor_field = populated[0]
    for spec in ANALYTICS_UNIQUE_SPECS:
        if spec.model_name == model_name and spec.actor_field == actor_field:
            if 'game_instance_id' in spec.columns and not lookup.get('game_instance_id'):
                raise AnalyticsRowInvariantError(
                    '{} create lookup is missing game_instance_id'.format(model_name)
                )
            return spec
    raise AnalyticsRowInvariantError(
        '{} is not a supported analytics persistence model'.format(model_name)
    )


def read_exact_analytics_row(model, lookup, *, using=None, for_update=False):
    alias = using or model._default_manager.db
    queryset = model._default_manager.using(alias).filter(**lookup).order_by('pk')
    if for_update:
        queryset = queryset.select_for_update()
    rows = list(queryset[:2])
    if len(rows) > 1:
        spec = analytics_unique_spec(model, lookup)
        raise AnalyticsRowInvariantError(
            '{} has multiple rows for {}'.format(model._meta.object_name, spec.index_name)
        )
    return rows[0] if rows else None


def _database_error(error):
    return getattr(error, '__cause__', None) or error


def _mysql_duplicate_matches(error, spec):
    cause = _database_error(error)
    args = getattr(cause, 'args', ())
    if not args or args[0] != 1062:
        return False
    message = str(args[1] if len(args) > 1 else cause)
    match = re.search(r"for key ['`]([^'`]+)['`]", message, flags=re.IGNORECASE)
    if match is None:
        return False
    actual_name = match.group(1).rsplit('.', 1)[-1]
    return actual_name == spec.index_name


def _sqlite_duplicate_matches(error, spec):
    cause = _database_error(error)
    message = str(cause)
    prefix = 'UNIQUE constraint failed:'
    if prefix not in message:
        return False
    raw_columns = message.split(prefix, 1)[1].strip().split(',')
    columns = tuple(item.strip().rsplit('.', 1)[-1] for item in raw_columns)
    return columns == spec.columns


def is_expected_analytics_duplicate(error, spec, *, using='default'):
    vendor = connections[using].vendor
    if vendor == 'mysql':
        return _mysql_duplicate_matches(error, spec)
    if vendor == 'sqlite':
        return _sqlite_duplicate_matches(error, spec)
    if vendor == 'postgresql':
        cause = _database_error(error)
        diag = getattr(cause, 'diag', None)
        return getattr(diag, 'constraint_name', None) == spec.index_name
    return False


def create_or_reread_analytics_row(model, *, lookup, defaults=None, using=None):
    """Create a logical analytics row or return the exact concurrent winner.

    Only a duplicate raised by the future namespace-specific unique index is
    recoverable. The local atomic block is deliberately narrower than the
    caller's transaction: its rollback clears the broken savepoint before the
    canonical row is read. Without a physical unique index, simultaneous first
    inserts can still both succeed; stage 1B.2 supplies that database guarantee.
    """
    alias = using or model._default_manager.db
    spec = analytics_unique_spec(model, lookup)
    canonical = read_exact_analytics_row(model, lookup, using=alias)
    if canonical is not None:
        return canonical, False

    values = dict(defaults or {})
    overlap = set(values).intersection(lookup)
    if overlap:
        raise ValueError('analytics create defaults overlap exact lookup')
    values.update(lookup)

    try:
        with transaction.atomic(using=alias):
            created = model._default_manager.using(alias).create(**values)
    except IntegrityError as error:
        if not is_expected_analytics_duplicate(error, spec, using=alias):
            raise
        canonical = read_exact_analytics_row(model, lookup, using=alias)
        if canonical is None:
            raise error.with_traceback(error.__traceback__)
        return canonical, False
    return created, True


def _copy_fields(target, source, fields):
    updated = []
    for field in fields:
        value = getattr(source, field)
        if getattr(target, field) != value:
            setattr(target, field, value)
            updated.append(field)
    return updated


def _source_has_earlier_timestamp(target, source, field):
    source_value = getattr(source, field)
    target_value = getattr(target, field)
    return source_value is not None and (
        target_value is None or source_value < target_value
    )


def _assert_same_event_placement(target, source):
    for row in (target, source):
        expected_instance_id = '{}:{}'.format(row.game_id, row.task_group_id)
        if row.game_instance_id != expected_instance_id:
            raise AnalyticsRowInvariantError(
                '{} has an invalid game placement'.format(row._meta.object_name)
            )
    if (
        target.game_id != source.game_id
        or target.task_group_id != source.task_group_id
        or target.game_instance_id != source.game_instance_id
    ):
        raise AnalyticsRowInvariantError(
            '{} collision has inconsistent placement fields'.format(
                target._meta.object_name,
            )
        )


def _assert_single_identity(row):
    populated = sum(
        value is not None
        for value in (row.user_id, row.anon_key, row.team_id)
    )
    if populated != 1:
        raise AnalyticsRowInvariantError(
            '{} row has an invalid analytics identity'.format(
                row._meta.object_name,
            )
        )


def _start_goal_signature(row):
    return (
        row.game_kind,
        row.public_game_id or row.game_instance_id,
    )


def _completion_goal_signature(row):
    return (
        row.game_kind,
        row.result,
        row.public_game_id or row.game_instance_id,
    )


def _merge_delivery_ack(
    target,
    source,
    *,
    target_signature_before,
    source_signature,
    final_signature,
):
    """Choose an ACK only when it proves delivery of the final Yandex goal.

    The physical row id is used by our browser delivery/idempotency key and by
    the signed callback token, but Yandex receives only the goal name and its
    params. A source ACK may therefore move only when every persisted param of
    the delivered source goal equals the final canonical goal. If equivalence
    cannot be proved, clearing the ACK can cause a safe repeat delivery; keeping
    it could falsely suppress a goal that was never delivered.
    """
    if (
        target.metrika_acked_at is not None
        and target_signature_before == final_signature
    ):
        return target.metrika_acked_at
    if source.metrika_acked_at is not None and source_signature == final_signature:
        return source.metrika_acked_at
    return None


def merge_started_analytics_rows(target, source):
    _assert_same_event_placement(target, source)
    target_signature_before = _start_goal_signature(target)
    source_signature = _start_goal_signature(source)
    updated = []
    if _source_has_earlier_timestamp(target, source, 'started_at'):
        updated.extend(_copy_fields(
            target,
            source,
            (
                'started_at',
                'game_kind',
                'public_game_id',
                'is_backfilled',
                'instrumentation_version',
            ),
        ))
    final_ack = _merge_delivery_ack(
        target,
        source,
        target_signature_before=target_signature_before,
        source_signature=source_signature,
        final_signature=_start_goal_signature(target),
    )
    if target.metrika_acked_at != final_ack:
        target.metrika_acked_at = final_ack
        updated.append('metrika_acked_at')
    if updated:
        target.save(update_fields=sorted(set(updated)))
    source.delete()
    return target


def merge_completed_analytics_rows(target, source):
    _assert_same_event_placement(target, source)
    target_signature_before = _completion_goal_signature(target)
    source_signature = _completion_goal_signature(source)
    updated = []
    if _source_has_earlier_timestamp(target, source, 'completed_at'):
        updated.extend(_copy_fields(
            target,
            source,
            (
                'completed_at',
                'game_kind',
                'public_game_id',
                'result',
                'is_backfilled',
                'instrumentation_version',
            ),
        ))
    final_ack = _merge_delivery_ack(
        target,
        source,
        target_signature_before=target_signature_before,
        source_signature=source_signature,
        final_signature=_completion_goal_signature(target),
    )
    if target.metrika_acked_at != final_ack:
        target.metrika_acked_at = final_ack
        updated.append('metrika_acked_at')
    if updated:
        target.save(update_fields=sorted(set(updated)))
    source.delete()
    return target


def merge_analytics_state_rows(target, source):
    updated = []
    if _source_has_earlier_timestamp(target, source, 'signup_at'):
        updated.extend(_copy_fields(
            target,
            source,
            ('signup_at', 'signup_method', 'signup_goal_acked_at'),
        ))
    if _source_has_earlier_timestamp(target, source, 'activated_at'):
        # The delivered activation payload contains games_completed, which is
        # not persisted. A source ACK therefore cannot prove delivery of the
        # final target payload even though the physical row id is only an
        # internal key. Keep the chosen timestamp/provenance together, but
        # clear the unprovable source ACK; merge itself must not redeliver it.
        updated.extend(_copy_fields(
            target,
            source,
            (
                'activated_at',
                'activation_is_backfilled',
            ),
        ))
        if target.activation_goal_acked_at is not None:
            target.activation_goal_acked_at = None
            updated.append('activation_goal_acked_at')
    if updated:
        target.save(update_fields=sorted(set(updated)) + ['updated_at'])
    source.delete()
    return target


def reassign_or_merge_analytics_row(
    source,
    *,
    target_lookup,
    identity_values,
    identity_update_fields,
    merge_rows,
):
    """Reassign source, merging first if the future unique key would collide."""
    model = type(source)
    alias = source._state.db or model._default_manager.db
    spec = analytics_unique_spec(model, target_lookup)
    _assert_single_identity(source)
    target = read_exact_analytics_row(
        model,
        target_lookup,
        using=alias,
        for_update=True,
    )
    if target is not None:
        _assert_single_identity(target)
        return merge_rows(target, source)

    for field, value in identity_values.items():
        setattr(source, field, value)
    try:
        with transaction.atomic(using=alias):
            source.save(update_fields=identity_update_fields)
    except IntegrityError as error:
        if not is_expected_analytics_duplicate(error, spec, using=alias):
            raise
        source.refresh_from_db()
        target = read_exact_analytics_row(
            model,
            target_lookup,
            using=alias,
            for_update=True,
        )
        if target is None:
            raise error.with_traceback(error.__traceback__)
        _assert_single_identity(target)
        return merge_rows(target, source)
    source.refresh_from_db()
    return source
