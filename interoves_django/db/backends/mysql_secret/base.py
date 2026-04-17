from __future__ import annotations

from django.db.backends.mysql.base import DatabaseWrapper as MySQLDatabaseWrapper
from django.db.utils import OperationalError

from interoves_django.rds_secret import get_rds_password


def _is_mysql_access_denied(exc: BaseException) -> bool:
    # mysqlclient exposes (errno, message, ...) in args
    args = getattr(exc, "args", None)
    if not args or not isinstance(args, (tuple, list)):
        return False
    try:
        errno = int(args[0])
    except Exception:
        return False
    return errno == 1045


class DatabaseWrapper(MySQLDatabaseWrapper):
    """
    MySQL backend that fetches PASSWORD from Secrets Manager at connection time.

    - Caches the secret in-process for a short TTL.
    - On (1045) access denied during connect, force-refreshes the secret and retries once.
    """

    def get_connection_params(self):
        conn_params = super().get_connection_params()
        # Django's MySQL backend uses 'passwd' for mysqlclient; keep compatibility
        password = get_rds_password()
        if "passwd" in conn_params:
            conn_params["passwd"] = password
        elif "password" in conn_params:
            conn_params["password"] = password
        return conn_params

    def get_new_connection(self, conn_params):
        try:
            return super().get_new_connection(conn_params)
        except OperationalError as e:
            if not _is_mysql_access_denied(e):
                raise
            # Secret may have rotated; refresh and retry once.
            password = get_rds_password(force_refresh=True)
            conn_params = dict(conn_params)
            if "passwd" in conn_params:
                conn_params["passwd"] = password
            elif "password" in conn_params:
                conn_params["password"] = password
            return super().get_new_connection(conn_params)

