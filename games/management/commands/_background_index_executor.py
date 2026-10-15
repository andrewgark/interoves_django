"""Shared, serialized executor for indexes deferred from Django migrations."""

import re
import sys
from contextlib import contextmanager

from django.core.management.base import CommandError
from django.db import connection


LOCK_NAME = "interoves:background-schema-work"
LOCK_TIMEOUT_SECONDS = 30
_INDEX_COLUMNS_RE = re.compile(r"\bON\s+`?\w+`?\s*\(([^()]*)\)", re.IGNORECASE)


def _expected_columns(ddl):
    match = _INDEX_COLUMNS_RE.search(ddl)
    if not match:
        raise CommandError("Cannot parse expected index columns from DDL.")
    return [column.strip().strip("`") for column in match.group(1).split(",")]


def _read_index(cursor, schema, table, name):
    cursor.execute(
        """
        SELECT column_name, seq_in_index, non_unique
        FROM information_schema.statistics
        WHERE table_schema = %s AND table_name = %s AND index_name = %s
        ORDER BY seq_in_index
        """,
        [schema, table, name],
    )
    return cursor.fetchall()


def _assert_lock_owned(cursor, native_connection):
    if connection.connection is not native_connection:
        raise CommandError("MySQL connection changed; schema-work lock may have been lost.")
    cursor.execute("SELECT IS_USED_LOCK(%s), CONNECTION_ID()", [LOCK_NAME])
    owner_id, connection_id = cursor.fetchone()
    if owner_id is None or owner_id != connection_id:
        raise CommandError("Lost MySQL schema-work lock; refusing to continue.")


def assert_schema_work_lock(native_connection):
    with connection.cursor() as cursor:
        _assert_lock_owned(cursor, native_connection)


@contextmanager
def schema_work_lock(command):
    """Serialize all participating executors across hosts through MySQL."""
    if connection.vendor != "mysql":
        raise CommandError("Controlled background schema work requires MySQL.")

    connection.ensure_connection()
    native_connection = connection.connection
    acquired = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, %s)", [LOCK_NAME, LOCK_TIMEOUT_SECONDS])
            acquired = cursor.fetchone()[0] == 1
        if not acquired:
            raise CommandError(
                "Could not acquire the global schema-work lock within "
                f"{LOCK_TIMEOUT_SECONDS} seconds."
            )
        if connection.connection is not native_connection:
            raise CommandError("MySQL connection changed while acquiring schema-work lock.")
        command.stdout.write("Acquired global MySQL schema-work lock.")
        yield native_connection
    finally:
        if acquired:
            active_exception = sys.exc_info()[0] is not None
            if connection.connection is not native_connection:
                message = "MySQL connection changed; cannot verify/release schema-work lock."
                if active_exception:
                    command.stderr.write("Warning: " + message)
                else:
                    raise CommandError(message)
            else:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT RELEASE_LOCK(%s)", [LOCK_NAME])
                        released = cursor.fetchone()[0]
                    if released != 1:
                        message = "MySQL schema-work lock was not released explicitly."
                        if active_exception:
                            command.stderr.write("Warning: " + message)
                        else:
                            raise CommandError(message)
                except Exception as exc:  # connection teardown also releases named locks
                    if active_exception:
                        command.stderr.write(f"Warning: could not release MySQL schema-work lock: {exc}")
                    else:
                        raise CommandError(
                            f"Could not release MySQL schema-work lock: {exc}"
                        ) from exc


def ensure_indexes(command, index_ddl, schema, native_connection):
    """Create only missing indexes; reject same-name indexes with wrong columns."""
    for table, name, ddl in index_ddl:
        expected_columns = _expected_columns(ddl)
        with connection.cursor() as cursor:
            _assert_lock_owned(cursor, native_connection)
            rows = _read_index(cursor, schema, table, name)
        actual_columns = [row[0] for row in rows]
        if rows and (actual_columns != expected_columns or any(row[2] != 1 for row in rows)):
            raise CommandError(
                f"Existing index {table}.{name} has definition {actual_columns}; "
                f"expected non-unique columns {expected_columns}. Refusing DDL."
            )
        if rows:
            command.stdout.write(f"OK {table}.{name} ({', '.join(actual_columns)})")
            continue

        command.stdout.write(f"Creating {table}.{name} with online DDL.")
        with connection.cursor() as cursor:
            _assert_lock_owned(cursor, native_connection)
            cursor.execute(ddl)
        with connection.cursor() as cursor:
            _assert_lock_owned(cursor, native_connection)
            rows = _read_index(cursor, schema, table, name)
        actual_columns = [row[0] for row in rows]
        if actual_columns != expected_columns or any(row[2] != 1 for row in rows):
            raise CommandError(
                f"Post-DDL verification failed for {table}.{name}: "
                f"found {actual_columns}, expected {expected_columns}."
            )
        command.stdout.write(command.style.SUCCESS(f"Verified {table}.{name}."))
