import json
import os
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class _CachedSecret:
    password: str
    fetched_at: float


_lock = threading.Lock()
_cache: _CachedSecret | None = None


def _cache_ttl_seconds() -> float:
    raw = (os.environ.get("RDS_SECRET_CACHE_TTL_SECONDS") or "").strip()
    if not raw:
        return 300.0
    try:
        v = float(raw)
    except ValueError:
        return 300.0
    return max(0.0, v)


def get_rds_password(*, force_refresh: bool = False) -> str:
    """
    Return the RDS password from AWS Secrets Manager, with a small in-process cache.

    Motivation: on EB, Secrets Manager can rotate; storing PASSWORD in settings at
    import time makes long-running workers keep using the old password until restart.
    """
    secret_arn = (os.environ.get("RDS_SECRET_ARN") or "").strip()
    if not secret_arn:
        # Caller should fall back to RDS_PASSWORD when ARN isn't set.
        return (os.environ.get("RDS_PASSWORD") or "")

    ttl = _cache_ttl_seconds()
    now = time.time()

    with _lock:
        global _cache
        if (
            not force_refresh
            and _cache is not None
            and ttl > 0.0
            and (now - _cache.fetched_at) <= ttl
            and _cache.password
        ):
            return _cache.password

        import boto3

        sm = boto3.client(
            "secretsmanager",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-central-1"),
        )
        secret_string = sm.get_secret_value(SecretId=secret_arn)["SecretString"]
        payload = json.loads(secret_string)
        password = (payload.get("password") or "").strip()
        if not password:
            raise RuntimeError("Secrets Manager secret does not contain a password field.")

        _cache = _CachedSecret(password=password, fetched_at=now)
        return password

