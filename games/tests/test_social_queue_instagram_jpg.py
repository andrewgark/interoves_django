from datetime import date
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from games.social.models import SocialQueuePost
from games.storage_backends import ProxyMediaStorage, social_queue_storage


def _png_bytes(size=(32, 40)):
    buf = BytesIO()
    Image.new('RGB', size, (10, 20, 30)).save(buf, format='PNG')
    return buf.getvalue()


class SocialQueueStorageTests(TestCase):
    @override_settings(SOCIAL_QUEUE_S3_BUCKET='')
    def test_without_bucket_uses_default_storage(self):
        self.assertIs(social_queue_storage(), default_storage)

    @override_settings(SOCIAL_QUEUE_S3_BUCKET='interoves-django-static')
    def test_with_bucket_uses_public_s3_storage(self):
        storage = social_queue_storage()
        self.assertIsInstance(storage, ProxyMediaStorage)
        self.assertEqual(storage.bucket_name, 'interoves-django-static')


@override_settings(SOCIAL_QUEUE_S3_BUCKET='')
class SocialQueueInstagramJpgTests(TestCase):
    def test_missing_file_is_404(self):
        post = SocialQueuePost.objects.create(
            caption='missing',
            source=SocialQueuePost.SOURCE_MANUAL,
        )
        post.image.save('missing.png', ContentFile(_png_bytes()), save=True)
        post.image.storage.delete(post.image.name)
        post.refresh_from_db()
        self.assertTrue(post.image.name)
        self.assertFalse(post.image.storage.exists(post.image.name))

        response = Client().get(reverse('social_queue_instagram_jpg', args=[post.pk]))
        self.assertEqual(response.status_code, 404)

    def test_renders_jpeg(self):
        post = SocialQueuePost.objects.create(
            caption='ok',
            source=SocialQueuePost.SOURCE_MANUAL,
        )
        post.image.save('ok.png', ContentFile(_png_bytes()), save=True)

        response = Client().get(reverse('social_queue_instagram_jpg', args=[post.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertTrue(response.content.startswith(b'\xff\xd8'))

    @patch('games.telegram.word_salad_image.render_word_salad_teaser_png')
    @patch('games.telegram.word_salad_channel.resolve_salad_by_number')
    def test_missing_salad_file_is_rebuilt(self, resolve, render):
        render.return_value = _png_bytes()
        resolve.return_value = SimpleNamespace(task=object(), number=36)
        post = SocialQueuePost.objects.create(
            caption='salad',
            source=SocialQueuePost.SOURCE_WORD_SALAD,
            ladder_date=date(2026, 9, 27),
            ladder_number=36,
        )
        post.image.save('salad-36.png', ContentFile(_png_bytes()), save=True)
        post.image.storage.delete(post.image.name)

        response = Client().get(reverse('social_queue_instagram_jpg', args=[post.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        post.refresh_from_db()
        self.assertTrue(post.image.storage.exists(post.image.name))
        render.assert_called_once()
