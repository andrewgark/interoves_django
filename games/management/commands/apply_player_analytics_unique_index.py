from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections

from games.analytics_persistence import (
    ANALYTICS_UNIQUE_SPEC_BY_NAME,
)
from games.management.commands._player_analytics_unique_indexes import (
    _mysql_indexes,
    collect_preflight,
    index_signature_matches,
)


def _same_unique_columns(index, spec):
    return (
        index['non_unique'] == 0
        and str(index['index_type']).upper() == 'BTREE'
        and tuple(column['name'] for column in index['columns']) == spec.columns
        and all(value is None for value in index['sub_parts'])
        and all(value is None for value in index['expressions'])
    )


def _database_error_code(error):
    cause = getattr(error, '__cause__', None) or error
    args = getattr(cause, 'args', ())
    return args[0] if args and isinstance(args[0], int) else 'UNKNOWN'


class Command(BaseCommand):
    help = (
        'Create exactly one approved player analytics unique index on MySQL. '
        'The command never chooses an index or a DDL fallback automatically.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--index',
            required=True,
            choices=tuple(sorted(ANALYTICS_UNIQUE_SPEC_BY_NAME)),
        )
        parser.add_argument('--database', default='default')
        parser.add_argument('--execute', action='store_true')
        parser.add_argument(
            '--confirm-free-storage-reviewed',
            action='store_true',
        )
        parser.add_argument(
            '--confirm-operational-checks-reviewed',
            action='store_true',
            help='Required only when transaction/metadata-lock visibility is unavailable.',
        )

    def _existing_status(self, connection, spec, table_name):
        indexes = _mysql_indexes(connection, table_name)
        named = indexes.get(spec.index_name)
        if named is not None:
            if index_signature_matches(named, spec):
                return 'EXACT', None
            return 'MISMATCH', spec.index_name
        equivalents = [
            name for name, index in indexes.items()
            if _same_unique_columns(index, spec)
        ]
        if equivalents:
            return 'EQUIVALENT_OTHER_NAME', equivalents[0]
        return 'ABSENT', None

    def _assert_operational_gate(self, report, options):
        if not report['online_ddl']['candidate_supported']:
            raise CommandError(
                'explicit ALGORITHM=INPLACE LOCK=NONE is not preflight-eligible'
            )
        transactions = report['transactions']
        metadata_locks = report['metadata_locks']
        unavailable = (
            transactions.get('status') == 'UNAVAILABLE'
            or metadata_locks.get('status') == 'UNAVAILABLE'
        )
        if unavailable and not options['confirm_operational_checks_reviewed']:
            raise CommandError(
                'transaction or metadata-lock visibility is unavailable; manual review required'
            )
        if transactions.get('at_least_30_seconds', 0):
            raise CommandError('long transactions are present; DDL was not started')
        if metadata_locks.get('pending', 0) or metadata_locks.get('blocker_pairs', 0):
            raise CommandError('metadata lock blockers are present; DDL was not started')
        if not options['confirm_free_storage_reviewed']:
            raise CommandError(
                'RDS FreeStorageSpace is UNAVAILABLE; manual confirmation is required'
            )

    def handle(self, *args, **options):
        alias = options['database']
        connection = connections[alias]
        if connection.vendor != 'mysql':
            raise CommandError('controlled production DDL is supported only on MySQL')
        if connection.in_atomic_block:
            raise CommandError('controlled DDL cannot run inside a transaction')

        report = collect_preflight(using=alias)
        if report['status'] != 'PASS_DATA_CHECKS':
            raise CommandError(
                'DDL blocked by duplicate groups or identity violations'
            )

        spec = ANALYTICS_UNIQUE_SPEC_BY_NAME[options['index']]
        model = apps.get_model('games', spec.model_name)
        table_name = model._meta.db_table
        status, conflicting_name = self._existing_status(
            connection, spec, table_name,
        )
        if status == 'EXACT':
            self.stdout.write('{}: SKIPPED_ALREADY_PRESENT'.format(spec.index_name))
            return
        if status == 'MISMATCH':
            raise CommandError('named index has a mismatched definition; DDL was not started')
        if status == 'EQUIVALENT_OTHER_NAME':
            raise CommandError(
                'equivalent unique index exists under another name; DDL was not started'
            )
        if not options['execute']:
            self.stdout.write('{}: READY_NOT_EXECUTED'.format(spec.index_name))
            return

        self._assert_operational_gate(report, options)
        quote = connection.ops.quote_name
        ddl = 'CREATE UNIQUE INDEX {} ON {} ({}) ALGORITHM=INPLACE LOCK=NONE'.format(
            quote(spec.index_name),
            quote(table_name),
            ', '.join(quote(column) for column in spec.columns),
        )
        previous_lock_wait_timeout = None
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT @@SESSION.lock_wait_timeout')
                previous_lock_wait_timeout = int(cursor.fetchone()[0])
                cursor.execute('SET SESSION lock_wait_timeout = 30')
                cursor.execute(ddl)
        except DatabaseError as error:
            raise CommandError(
                'controlled DDL failed with database error code {}; inspect server state '
                'and rerun exact verification before retrying'.format(
                    _database_error_code(error),
                )
            ) from None
        finally:
            if previous_lock_wait_timeout is not None and connection.is_usable():
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            'SET SESSION lock_wait_timeout = %s',
                            [previous_lock_wait_timeout],
                        )
                except DatabaseError:
                    pass

        status, _conflicting_name = self._existing_status(connection, spec, table_name)
        if status != 'EXACT':
            raise CommandError('post-DDL exact index verification failed')
        postflight = collect_preflight(using=alias)
        if postflight['duplicate_groups'][spec.index_name] != 0:
            raise CommandError('post-DDL duplicate verification failed')
        self.stdout.write(self.style.SUCCESS('{}: CREATED_AND_VERIFIED'.format(
            spec.index_name,
        )))
