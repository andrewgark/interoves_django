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


def remember_working_password(password: str) -> None:
    """Keep a password that just authenticated, so later connects skip 1045."""
    password = (password or "").strip()
    if not password:
        return
    global _cache
    with _lock:
        _cache = _CachedSecret(password=password, fetched_at=time.time())


def _parse_secret_password(secret_string: str) -> str:
    payload = json.loads(secret_string)
    return (payload.get("password") or "").strip()


def get_rds_password(*, force_refresh: bool = False, version_stage: str | None = None) -> str:
    """
    Return the RDS password from AWS Secrets Manager, with a small in-process cache.

    Motivation: on EB, Secrets Manager can rotate; storing PASSWORD in settings at
    import time makes long-running workers keep using the old password until restart.
    """
    secret_arn = (os.environ.get("RDS_SECRET_ARN") or "").strip()
    if not secret_arn:
        # Caller should fall back to RDS_PASSWORD when ARN isn't set.
        return (os.environ.get("RDS_PASSWORD") or "")

    stage = (version_stage or "AWSCURRENT").strip() or "AWSCURRENT"
    is_current = stage == "AWSCURRENT"
    ttl = _cache_ttl_seconds()
    now = time.time()

    with _lock:
        global _cache
        if (
            is_current
            and not force_refresh
            and _cache is not None
            and ttl > 0.0
            and (now - _cache.fetched_at) <= ttl
            and _cache.password
        ):
            return _cache.password

        import boto3
        from botocore.exceptions import ClientError

        sm = boto3.client(
            "secretsmanager",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-central-1"),
        )
        kwargs = {"SecretId": secret_arn}
        if not is_current:
            kwargs["VersionStage"] = stage
        try:
            secret_string = sm.get_secret_value(**kwargs)["SecretString"]
        except ClientError as exc:
            code = (exc.response or {}).get("Error", {}).get("Code", "")
            if not is_current and code in {
                "ResourceNotFoundException",
                "ResourceNotFound",
            }:
                return ""
            raise
        password = _parse_secret_password(secret_string)
        if not password:
            if is_current:
                raise RuntimeError("Secrets Manager secret does not contain a password field.")
            return ""

        if is_current:
            _cache = _CachedSecret(password=password, fetched_at=now)
        return password


def passwords_after_access_denied() -> list[str]:
    """
    Unique passwords to try after MySQL 1045 (stale cache or in-flight rotation).
    """
    seen: set[str] = set()
    out: list[str] = []
    for stage in (None, "AWSPENDING", "AWSPREVIOUS"):
        try:
            password = get_rds_password(force_refresh=True, version_stage=stage)
        except Exception:
            if stage:
                continue
            raise
        if password and password not in seen:
            seen.add(password)
            out.append(password)
    return out
