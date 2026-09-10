from __future__ import annotations

import logging

from django.db.backends.mysql.base import DatabaseWrapper as MySQLDatabaseWrapper

from interoves_django.rds_secret import (
    get_rds_password,
    passwords_after_access_denied,
    remember_working_password,
)

logger = logging.getLogger(__name__)


def _is_mysql_access_denied(exc: BaseException) -> bool:
    # mysqlclient exposes (errno, message, ...) in args
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        args = getattr(current, "args", None)
        if args and isinstance(args, (tuple, list)):
            try:
                if int(args[0]) == 1045:
                    return True
            except Exception:
                pass
        current = current.__cause__ or current.__context__
    return False


def connect_with_secret_retry(connect, conn_params):
    """
    Call ``connect(conn_params)``, refreshing the RDS secret on MySQL 1045.

    mysqlclient raises ``MySQLdb.OperationalError``, which is *not* a subclass of
    ``django.db.utils.OperationalError``. Django wraps it only after
    ``get_new_connection`` returns, so the retry must catch the raw driver error.
    """
    try:
        return connect(conn_params)
    except Exception as exc:
        if not _is_mysql_access_denied(exc):
            raise
        last_error = exc
        logger.warning("MySQL 1045 on connect; refreshing RDS secret and retrying")
        for password in passwords_after_access_denied():
            retry_params = dict(conn_params)
            retry_params["passwd"] = password
            try:
                connection = connect(retry_params)
            except Exception as retry_exc:
                if not _is_mysql_access_denied(retry_exc):
                    raise
                last_error = retry_exc
                continue
            remember_working_password(password)
            return connection
        raise last_error


class DatabaseWrapper(MySQLDatabaseWrapper):
    """
    MySQL backend that fetches PASSWORD from Secrets Manager at connection time.

    - Caches the secret in-process for a short TTL.
    - On (1045) access denied during connect, force-refreshes the secret and retries.
    """

    def get_connection_params(self):
        conn_params = super().get_connection_params()
        # Ensure mysqlclient receives a password even if Django omitted the key
        # because settings.PASSWORD was empty.
        password = get_rds_password()
        conn_params["passwd"] = password
        return conn_params

    def get_new_connection(self, conn_params):
        return connect_with_secret_retry(super().get_new_connection, conn_params)
