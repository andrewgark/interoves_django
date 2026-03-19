from storages.backends.s3boto3 import S3Boto3Storage
from django.conf import settings


class StaticStorage(S3Boto3Storage):
    location = 'static'
    default_acl = 'public-read'


class PublicMediaStorage(S3Boto3Storage):
    location = 'media'
    default_acl = 'public-read'
    file_overwrite = False


class ProxyMediaStorage(PublicMediaStorage):
    """
    Store media in S3, but serve it via the app domain (/media/...) where Nginx
    reverse-proxies to S3. This keeps URLs stable and first-party.
    """

    def url(self, name, parameters=None, expire=None, http_method=None):
        name = (name or "").lstrip("/")
        return f"/media/{name}"
