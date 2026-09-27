"""Health checks that match what the web role is allowed to do.

The public web role can read ``interoves-django-static`` and list the bucket.
It cannot ``s3:PutObject``, so the stock django-health-check storage probe
(write, read, delete a throwaway object) always fails on the live site.
"""

from botocore.exceptions import ClientError
from health_check.exceptions import ServiceUnavailable
from health_check.storage.backends import StorageHealthCheck
from storages.backends.s3 import S3Storage


class MediaStorageHealthCheck(StorageHealthCheck):
    storage_alias = 'default'

    def check_status(self):
        storage = self.get_storage()
        if storage is None:
            raise ServiceUnavailable('default storage is not configured')
        if isinstance(storage, S3Storage):
            return self._check_s3(storage)
        return super().check_status()

    def _check_s3(self, storage):
        bucket = getattr(storage, 'bucket_name', None) or ''
        if not bucket:
            raise ServiceUnavailable('S3 bucket is not configured')
        try:
            storage.connection.meta.client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            code = (exc.response.get('Error') or {}).get('Code') or 's3'
            raise ServiceUnavailable('S3 {}'.format(code)) from exc
        return True
