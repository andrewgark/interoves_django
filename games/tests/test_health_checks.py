import os
import tempfile
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.core.files.storage import FileSystemStorage
from django.test import SimpleTestCase
from health_check.exceptions import ServiceUnavailable
from storages.backends.s3 import S3Storage

from games.health_checks import MediaStorageHealthCheck


class MediaStorageHealthCheckTests(SimpleTestCase):
    def test_s3_probe_heads_the_bucket(self):
        storage = MagicMock(spec=S3Storage)
        storage.bucket_name = 'interoves-django-static'
        check = MediaStorageHealthCheck()
        with patch.object(check, 'get_storage', return_value=storage):
            self.assertTrue(check.check_status())
        storage.connection.meta.client.head_bucket.assert_called_once_with(
            Bucket='interoves-django-static',
        )

    def test_s3_access_denied_is_unavailable(self):
        storage = MagicMock(spec=S3Storage)
        storage.bucket_name = 'interoves-django-static'
        storage.connection.meta.client.head_bucket.side_effect = ClientError(
            {'Error': {'Code': '403', 'Message': 'Forbidden'}},
            'HeadBucket',
        )
        check = MediaStorageHealthCheck()
        with patch.object(check, 'get_storage', return_value=storage):
            with self.assertRaises(ServiceUnavailable):
                check.check_status()

    def test_filesystem_round_trip_removes_the_probe_file(self):
        check = MediaStorageHealthCheck()
        with tempfile.TemporaryDirectory() as tmp:
            storage = FileSystemStorage(location=tmp)
            with patch.object(check, 'get_storage', return_value=storage):
                self.assertTrue(check.check_status())
            leftover = []
            for root, _dirs, files in os.walk(tmp):
                leftover.extend(os.path.join(root, name) for name in files)
            self.assertEqual(leftover, [])
