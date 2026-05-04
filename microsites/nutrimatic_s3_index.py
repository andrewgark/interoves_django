"""
Fetch Nutrimatic *.index from S3 to a local file — find-expr only accepts a filesystem path.

Configure NUTRIMATIC_INDEX_S3_BUCKET + NUTRIMATIC_INDEX_S3_KEY. The file is cached under
nutrimatic_bundle/.s3_index_cache/ (same directory as bundle_microsites output).
"""
from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore


def _cache_dir() -> Path:
    return Path(settings.BASE_DIR) / "nutrimatic_bundle" / ".s3_index_cache"


def local_cache_path_for_key(key: str) -> Path:
    safe = key.replace("/", "__").lstrip("_")
    return _cache_dir() / safe


def _run_with_lock(lock_path: Path, fn):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lf:
        if fcntl is not None:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            if fcntl is not None:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def ensure_nutrimatic_index_from_s3(*, force: bool = False) -> Path | None:
    """
    Return path to a local copy of the index, downloading from S3 if missing (or if force).
    Returns None when S3 source is not configured.
    """
    bucket = (getattr(settings, "NUTRIMATIC_INDEX_S3_BUCKET", None) or "").strip()
    key = (getattr(settings, "NUTRIMATIC_INDEX_S3_KEY", None) or "").strip()
    if not bucket or not key:
        return None

    region = (getattr(settings, "NUTRIMATIC_INDEX_S3_REGION", None) or "").strip() or "eu-central-1"
    dest = local_cache_path_for_key(key)
    lock = _cache_dir() / ".download.lock"

    def do_download():
        if dest.is_file() and not force:
            return dest

        import boto3
        from botocore.exceptions import ClientError

        s3 = boto3.client("s3", region_name=region)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            s3.download_file(bucket, key, str(tmp))
        except ClientError:
            logger.exception(
                "Nutrimatic S3 index download failed (bucket=%s key=%s)", bucket, key
            )
            if tmp.is_file():
                tmp.unlink(missing_ok=True)
            raise
        tmp.replace(dest)
        return dest

    return _run_with_lock(lock, do_download)
