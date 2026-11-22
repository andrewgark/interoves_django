import asyncio
from io import BytesIO

from django.test import SimpleTestCase
from PIL import Image
from telethon.errors import ImageProcessFailedError

from games.ops.telegram.transport.mtproto import _retry_downscaled, downscale_photo


def _png(width: int, height: int) -> bytes:
    out = BytesIO()
    Image.new('RGB', (width, height), 'white').save(out, 'PNG')
    return out.getvalue()


class RetryDownscaledTests(SimpleTestCase):
    def test_downscale_fits_long_side(self):
        image = Image.open(BytesIO(downscale_photo(_png(1844, 1888))))
        self.assertEqual(max(image.size), 1280)

    def test_image_process_failed_retries_once_downscaled(self):
        sizes = []

        async def send(photo):
            sizes.append(Image.open(BytesIO(photo.getvalue())).size)
            self.assertEqual(photo.name, 'ladder-138.png')
            if len(sizes) == 1:
                raise ImageProcessFailedError(request=None)
            return 'sent'

        result = asyncio.run(_retry_downscaled(send, _png(1844, 1888), 'ladder-138.png'))

        self.assertEqual(result, 'sent')
        self.assertEqual(sizes[0], (1844, 1888))
        self.assertEqual(max(sizes[1]), 1280)

    def test_success_sends_original_only(self):
        calls = []

        async def send(photo):
            calls.append(photo)
            return 'sent'

        asyncio.run(_retry_downscaled(send, _png(1844, 1888), 'x.png'))
        self.assertEqual(len(calls), 1)
