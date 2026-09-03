import re

from django.apps import apps
from django.db import connections
from django.db.models import Count, Q

from games.analytics_persistence import ANALYTICS_UNIQUE_SPECS


TARGET_MODEL_NAMES = (
    'PlayerStartedGame',
    'PlayerCompletedGame',
    'PlayerAnalyticsState',
)
TARGET_TABLES = tuple(
    apps.get_model('games', model_name)._meta.db_table
    for model_name in TARGET_MODEL_NAMES
)


def _valid_identity_q():
    return (
        Q(user_id__isnull=False, team_id__isnull=True, anon_key__isnull=True)
        | Q(user_id__isnull=True, team_id__isnull=False, anon_key__isnull=True)
        | Q(user_id__isnull=True, team_id__isnull=True, anon_key__isnull=False)
    )


def _unavailable(reason):
    return {'status': 'UNAVAILABLE', 'reason': reason}


def _mysql_version_tuple(raw):
    match = re.match(r'^(\d+)\.(\d+)\.(\d+)', str(raw or ''))
    if match is None:
        return ()
    return tuple(int(value) for value in match.groups())


def _mysql_indexes(connection, table_name):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT INDEX_NAME, NON_UNIQUE, INDEX_TYPE, IS_VISIBLE,
                   SEQ_IN_INDEX, COLUMN_NAME, SUB_PART, EXPRESSION
            FROM information_schema.statistics
            WHERE table_schema = DATABASE() AND table_name = %s
            ORDER BY INDEX_NAME, SEQ_IN_INDEX
            """,
            [table_name],
        )
        rows = cursor.fetchall()
    indexes = {}
    for (
        name,
        non_unique,
        index_type,
        is_visible,
        sequence,
        column,
        sub_part,
        expression,
    ) in rows:
        current = indexes.setdefault(name, {
            'name': name,
            'non_unique': int(non_unique),
            'index_type': index_type,
            'is_visible': is_visible,
            'columns': [],
            'sub_parts': [],
            'expressions': [],
        })
        current['columns'].append({'position': int(sequence), 'name': column})
        current['sub_parts'].append(sub_part)
        current['expressions'].append(expression)
    return indexes


def _portable_indexes(connection, table_name):
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table_name)
    indexes = {}
    for name, value in sorted(constraints.items()):
        if not value.get('index') and not value.get('unique'):
            continue
        indexes[name] = {
            'name': name,
            'non_unique': 0 if value.get('unique') else 1,
            'index_type': value.get('type') or 'UNKNOWN',
            'is_visible': 'UNKNOWN',
            'columns': [
                {'position': position, 'name': column}
                for position, column in enumerate(value.get('columns') or (), start=1)
            ],
            'sub_parts': [],
            'expressions': [],
        }
    return indexes


def index_signature_matches(index, spec):
    if index is None:
        return False
    return (
        index['name'] == spec.index_name
        and index['non_unique'] == 0
        and str(index['index_type']).upper() == 'BTREE'
        and str(index['is_visible']).upper() in ('YES', 'UNKNOWN')
        and tuple(column['name'] for column in index['columns']) == spec.columns
        and all(value is None for value in index['sub_parts'])
        and all(value is None for value in index['expressions'])
    )


def _duplicate_group_count(model, spec, *, using):
    return (
        model._default_manager.using(using)
        .filter(**{'{}__isnull'.format(spec.actor_field): False})
        .values(*spec.columns)
        .annotate(row_count=Count('pk'))
        .filter(row_count__gt=1)
        .count()
    )


def _mysql_table_details(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT TABLE_NAME, ENGINE, TABLE_COLLATION,
                   DATA_LENGTH, INDEX_LENGTH, DATA_FREE
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name IN (%s, %s, %s)
            ORDER BY TABLE_NAME
            """,
            list(TARGET_TABLES),
        )
        rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT TABLE_NAME, COLUMN_TYPE, IS_NULLABLE,
                   CHARACTER_SET_NAME, COLLATION_NAME
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name IN (%s, %s, %s)
              AND column_name = 'anon_key'
            ORDER BY TABLE_NAME
            """,
            list(TARGET_TABLES),
        )
        anon_columns = cursor.fetchall()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.referential_constraints
            WHERE constraint_schema = DATABASE()
              AND (
                  table_name IN (%s, %s, %s)
                  OR referenced_table_name IN (%s, %s, %s)
              )
              AND (update_rule IN ('CASCADE', 'SET NULL')
                   OR delete_rule IN ('CASCADE', 'SET NULL'))
            """,
            list(TARGET_TABLES) + list(TARGET_TABLES),
        )
        non_restricting_fk_count = int(cursor.fetchone()[0])
    details = {
        name: {
            'engine': engine,
            'collation': collation,
            'data_bytes': int(data_length or 0),
            'index_bytes': int(index_length or 0),
            'data_free_bytes': int(data_free or 0),
        }
        for name, engine, collation, data_length, index_length, data_free in rows
    }
    for name, column_type, nullable, charset, collation in anon_columns:
        details.setdefault(name, {})['anon_key'] = {
            'column_type': column_type,
            'nullable': nullable,
            'character_set': charset,
            'collation': collation,
        }
    return details, non_restricting_fk_count


def _mysql_transaction_details(connection):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*),
                       COALESCE(MAX(TIMESTAMPDIFF(SECOND, trx_started, NOW())), 0),
                       COALESCE(SUM(
                           TIMESTAMPDIFF(SECOND, trx_started, NOW()) >= 30
                       ), 0)
                FROM information_schema.innodb_trx
                """
            )
            count, max_age, long_count = cursor.fetchone()
        return {
            'status': 'AVAILABLE',
            'count': int(count),
            'max_age_seconds': int(max_age),
            'at_least_30_seconds': int(long_count),
        }
    except Exception:
        return _unavailable('insufficient database visibility')


def _mysql_metadata_lock_details(connection):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    SUM(lock_status = 'PENDING'),
                    SUM(lock_status = 'GRANTED')
                FROM performance_schema.metadata_locks
                WHERE object_schema = DATABASE()
                  AND object_name IN (%s, %s, %s)
                """,
                list(TARGET_TABLES),
            )
            pending, granted = cursor.fetchone()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM performance_schema.metadata_locks waiting
                JOIN performance_schema.metadata_locks granted
                  ON granted.object_type = waiting.object_type
                 AND granted.object_schema = waiting.object_schema
                 AND granted.object_name = waiting.object_name
                 AND granted.owner_thread_id <> waiting.owner_thread_id
                 AND granted.lock_status = 'GRANTED'
                WHERE waiting.object_schema = DATABASE()
                  AND waiting.object_name IN (%s, %s, %s)
                  AND waiting.lock_status = 'PENDING'
                """,
                list(TARGET_TABLES),
            )
            blocker_pairs = int(cursor.fetchone()[0])
        return {
            'status': 'AVAILABLE',
            'pending': int(pending or 0),
            'granted': int(granted or 0),
            'blocker_pairs': blocker_pairs,
        }
    except Exception:
        return _unavailable('insufficient performance_schema visibility')


def collect_preflight(*, using='default'):
    connection = connections[using]
    connection.ensure_connection()
    vendor = connection.vendor
    with connection.cursor() as cursor:
        if vendor == 'mysql':
            cursor.execute('SELECT VERSION()')
            server_version = str(cursor.fetchone()[0])
        else:
            server_version = str(getattr(connection.Database, 'sqlite_version', 'UNKNOWN'))

    tables = {}
    if vendor == 'mysql':
        table_details, non_restricting_fk_count = _mysql_table_details(connection)
    else:
        table_details, non_restricting_fk_count = {}, 0

    all_indexes = {}
    identity_violations = {}
    for model_name in TARGET_MODEL_NAMES:
        model = apps.get_model('games', model_name)
        table_name = model._meta.db_table
        table = dict(table_details.get(table_name, {}))
        table['exact_row_count'] = model._default_manager.using(using).count()
        tables[table_name] = table
        identity_violations[model_name] = (
            model._default_manager.using(using).exclude(_valid_identity_q()).count()
        )
        all_indexes[table_name] = (
            _mysql_indexes(connection, table_name)
            if vendor == 'mysql'
            else _portable_indexes(connection, table_name)
        )

    duplicate_groups = {}
    expected_indexes = {}
    for spec in ANALYTICS_UNIQUE_SPECS:
        model = apps.get_model('games', spec.model_name)
        table_name = model._meta.db_table
        duplicate_groups[spec.index_name] = _duplicate_group_count(
            model, spec, using=using,
        )
        actual = all_indexes[table_name].get(spec.index_name)
        expected_indexes[spec.index_name] = {
            'table': table_name,
            'columns': list(spec.columns),
            'status': 'EXACT' if index_signature_matches(actual, spec) else (
                'ABSENT' if actual is None else 'MISMATCH'
            ),
        }

    data_blocked = bool(
        any(duplicate_groups.values()) or any(identity_violations.values())
    )
    if vendor == 'mysql':
        transactions = _mysql_transaction_details(connection)
        metadata_locks = _mysql_metadata_lock_details(connection)
        version_supported = _mysql_version_tuple(server_version) >= (8, 4, 0)
        all_innodb = all(
            str(table.get('engine', '')).upper() == 'INNODB'
            for table in tables.values()
        )
        online_ddl = {
            'requested_algorithm': 'INPLACE',
            'requested_lock': 'NONE',
            'candidate_supported': bool(
                version_supported and all_innodb and non_restricting_fk_count == 0
            ),
            'non_restricting_fk_count': non_restricting_fk_count,
            'no_fallback': True,
        }
    else:
        transactions = _unavailable('MySQL-only diagnostic')
        metadata_locks = _unavailable('MySQL-only diagnostic')
        online_ddl = {
            'requested_algorithm': 'INPLACE',
            'requested_lock': 'NONE',
            'candidate_supported': False,
            'reason': 'production DDL is MySQL-only',
            'no_fallback': True,
        }

    return {
        'status': 'BLOCKED_DATA' if data_blocked else 'PASS_DATA_CHECKS',
        'database_vendor': vendor,
        'server_version': server_version,
        'duplicate_groups': duplicate_groups,
        'identity_violations': identity_violations,
        'tables': tables,
        'indexes': {
            table: list(indexes.values())
            for table, indexes in all_indexes.items()
        },
        'expected_indexes': expected_indexes,
        'free_storage_space': _unavailable(
            'RDS FreeStorageSpace is not available through the application database connection'
        ),
        'transactions': transactions,
        'metadata_locks': metadata_locks,
        'online_ddl': online_ddl,
    }
