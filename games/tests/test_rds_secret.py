from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from interoves_django.db.backends.mysql_secret.base import (
    _is_mysql_access_denied,
    connect_with_secret_retry,
)
from interoves_django import rds_secret


class _MysqlAccessDenied(Exception):
    def __init__(self, errno=1045, message="Access denied"):
        super().__init__(errno, message)


class _OtherMysqlError(Exception):
    def __init__(self):
        super().__init__(2006, "MySQL server has gone away")


class MysqlAccessDeniedTests(SimpleTestCase):
    def test_detects_mysqlclient_1045(self):
        self.assertTrue(_is_mysql_access_denied(_MysqlAccessDenied()))

    def test_detects_wrapped_1045(self):
        inner = _MysqlAccessDenied()
        outer = RuntimeError("wrapped")
        outer.__cause__ = inner
        self.assertTrue(_is_mysql_access_denied(outer))

    def test_ignores_other_errno(self):
        self.assertFalse(_is_mysql_access_denied(_OtherMysqlError()))


class ConnectWithSecretRetryTests(SimpleTestCase):
    def test_retries_raw_mysqlclient_1045_with_refreshed_password(self):
        calls = []

        def connect(conn_params):
            calls.append(conn_params.get("passwd"))
            if conn_params.get("passwd") == "stale":
                raise _MysqlAccessDenied()
            return "ok"

        with patch(
            "interoves_django.db.backends.mysql_secret.base.passwords_after_access_denied",
            return_value=["fresh"],
        ), patch(
            "interoves_django.db.backends.mysql_secret.base.remember_working_password",
        ) as remember:
            result = connect_with_secret_retry(connect, {"passwd": "stale"})

        self.assertEqual(result, "ok")
        self.assertEqual(calls, ["stale", "fresh"])
        remember.assert_called_once_with("fresh")

    def test_does_not_retry_non_1045(self):
        def connect(conn_params):
            raise _OtherMysqlError()

        with self.assertRaises(_OtherMysqlError):
            connect_with_secret_retry(connect, {"passwd": "stale"})

    def test_tries_pending_after_current_still_denied(self):
        calls = []

        def connect(conn_params):
            calls.append(conn_params.get("passwd"))
            if conn_params.get("passwd") != "pending":
                raise _MysqlAccessDenied()
            return "ok"

        with patch(
            "interoves_django.db.backends.mysql_secret.base.passwords_after_access_denied",
            return_value=["current", "pending"],
        ), patch(
            "interoves_django.db.backends.mysql_secret.base.remember_working_password",
        ) as remember:
            result = connect_with_secret_retry(connect, {"passwd": "stale"})

        self.assertEqual(result, "ok")
        self.assertEqual(calls, ["stale", "current", "pending"])
        remember.assert_called_once_with("pending")


class RdsSecretTests(SimpleTestCase):
    def setUp(self):
        rds_secret._cache = None

    def tearDown(self):
        rds_secret._cache = None

    def test_falls_back_to_env_password_without_arn(self):
        with patch.dict(
            "os.environ",
            {"RDS_PASSWORD": "from-env", "RDS_SECRET_ARN": ""},
        ):
            self.assertEqual(rds_secret.get_rds_password(), "from-env")

    def test_passwords_after_access_denied_are_unique_and_ordered(self):
        def fake_get(*, force_refresh=False, version_stage=None):
            self.assertTrue(force_refresh)
            if version_stage in (None, "AWSCURRENT"):
                return "current"
            if version_stage == "AWSPENDING":
                return "pending"
            if version_stage == "AWSPREVIOUS":
                return "current"
            return ""

        with patch.object(rds_secret, "get_rds_password", side_effect=fake_get):
            self.assertEqual(
                rds_secret.passwords_after_access_denied(),
                ["current", "pending"],
            )

    def test_pending_missing_secret_is_skipped(self):
        from botocore.exceptions import ClientError

        def fake_client(*args, **kwargs):
            sm = MagicMock()

            def get_secret_value(**kwargs):
                if kwargs.get("VersionStage") == "AWSPENDING":
                    raise ClientError(
                        {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
                        "GetSecretValue",
                    )
                return {"SecretString": '{"password": "current"}'}

            sm.get_secret_value.side_effect = get_secret_value
            return sm

        env = {
            "RDS_SECRET_ARN": "arn:aws:secretsmanager:eu-central-1:1:secret:x",
            "AWS_DEFAULT_REGION": "eu-central-1",
        }
        with patch.dict("os.environ", env, clear=False), patch(
            "boto3.client", side_effect=fake_client
        ):
            self.assertEqual(
                rds_secret.get_rds_password(force_refresh=True, version_stage="AWSPENDING"),
                "",
            )
            self.assertEqual(rds_secret.get_rds_password(force_refresh=True), "current")
