"""Where a player's photo comes from: a login provider or an uploaded file.

``Profile.avatar_url`` stays the single source of truth. Provider photos are
stored as the provider's own URL; uploads (and Telegram photos fetched by the
bot) are re-encoded here and kept in media storage under ``avatars/``.
"""

import io
import logging
import re
import uuid
from dataclasses import dataclass

import requests
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

UPLOAD_DIR = 'avatars'
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
# Decompression bombs: a tiny file can claim a huge canvas.
MAX_SOURCE_PIXELS = 40_000_000
AVATAR_SIDE = 400
_UPLOADED_NAME_RE = re.compile(r'^[0-9a-f]{32}\.jpg$')

SOURCE_LABELS = {
    'vk': 'VK',
    'telegram': 'Telegram',
    'google': 'Google',
    'yandex': 'Яндекс',
}
SOURCE_ORDER = ('vk', 'telegram', 'google', 'yandex')


@dataclass(frozen=True)
class AvatarSource:
    key: str
    label: str
    # Empty for Telegram linked only through the bot: the photo is fetched
    # when the player picks it, since Bot API file links carry the bot token.
    url: str
    # Other URLs of the same photo (sizes, the one saved at signup), so the
    # picker can mark the current source.
    aliases: tuple = ()

    def matches(self, url):
        return bool(url) and (url == self.url or url in self.aliases)

    @property
    def needs_fetch(self):
        return not self.url


# --- Provider photos -------------------------------------------------------

def _google_url(extra):
    url = extra.get('picture') or ''
    # Google hands out a 96px thumbnail; the size suffix is part of the URL.
    return re.sub(r'=s\d+(-c)?$', '=s400-c', url) if url else ''


def _yandex_url(extra):
    from games.site.http.signals import _avatar_url_from_extra

    return _avatar_url_from_extra(extra)


_PROVIDER_URL = {
    'vk': lambda extra: extra.get('photo_big') or extra.get('photo_max_orig') or extra.get('photo_medium') or '',
    'telegram': lambda extra: extra.get('picture') or '',
    'google': _google_url,
    'yandex': _yandex_url,
}


def provider_sources(user, profile=None):
    """Photos this player can pick from their connected accounts, VK first."""
    found = {}
    aliases = {}
    for account in SocialAccount.objects.filter(user=user).order_by('id'):
        pick = _PROVIDER_URL.get(account.provider)
        if pick is None or account.provider in found:
            continue
        extra = account.extra_data or {}
        url = (pick(extra) or '').strip()
        if url.startswith('https://'):
            found[account.provider] = url
            aliases[account.provider] = tuple(
                value for key, value in extra.items()
                if isinstance(value, str) and value.startswith('https://')
                and any(part in key for part in ('photo', 'picture'))
            )
    if (
        'telegram' not in found and profile is not None
        and profile.telegram_user_id and getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    ):
        found['telegram'] = ''
    return [
        AvatarSource(key, SOURCE_LABELS[key], found[key], aliases.get(key, ()))
        for key in SOURCE_ORDER if key in found
    ]


# --- Uploaded files --------------------------------------------------------

def _uploaded_prefix():
    return default_storage.url(UPLOAD_DIR + '/x.jpg')[:-len('x.jpg')]


def uploaded_name(url):
    """Storage name of an avatar we stored ourselves, else ``''``."""
    url = str(url or '')
    prefix = _uploaded_prefix()
    if not url.startswith(prefix):
        return ''
    tail = url[len(prefix):]
    return '{}/{}'.format(UPLOAD_DIR, tail) if _UPLOADED_NAME_RE.match(tail) else ''


def is_uploaded_avatar_url(url):
    return bool(uploaded_name(url))


def normalize_image(data):
    """Re-encode any raster image as a square JPEG; raise ValidationError."""
    from PIL import Image, ImageOps

    if len(data) > MAX_UPLOAD_BYTES:
        raise ValidationError('Файл больше 8 МБ.')
    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
            if width * height > MAX_SOURCE_PIXELS:
                raise ValidationError('Слишком большое изображение.')
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            image = ImageOps.exif_transpose(image)
            if image.mode in ('RGBA', 'LA', 'P'):
                image = image.convert('RGBA')
                background = Image.new('RGB', image.size, (255, 255, 255))
                background.paste(image, mask=image.getchannel('A'))
                image = background
            else:
                image = image.convert('RGB')
            image = ImageOps.fit(image, (AVATAR_SIDE, AVATAR_SIDE), Image.LANCZOS)
            out = io.BytesIO()
            # Re-encoding drops EXIF (GPS included) and anything that is not pixels.
            image.save(out, format='JPEG', quality=86, optimize=True)
            return out.getvalue()
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError('Не получилось прочитать картинку. Подойдут JPG, PNG, WebP или GIF.') from exc


def store_image(data):
    """Save normalized image bytes; return the public URL."""
    name = default_storage.save(
        '{}/{}.jpg'.format(UPLOAD_DIR, uuid.uuid4().hex), ContentFile(normalize_image(data)),
    )
    return default_storage.url(name)


def delete_uploaded(url):
    name = uploaded_name(url)
    if not name:
        return
    try:
        default_storage.delete(name)
    except Exception:
        logger.warning('Could not delete old avatar %s', name, exc_info=True)


# --- Telegram via the bot --------------------------------------------------

def fetch_telegram_photo(telegram_user_id):
    """Download the player's current Telegram photo through the Bot API.

    Returns raw bytes or ``None`` (no photo, hidden by privacy settings, or
    Telegram unavailable). The file URL contains the bot token, so it is
    never stored or shown.
    """
    from games.ops.telegram.transport.api import _api_url, _safe_error_text

    if not telegram_user_id or not getattr(settings, 'TELEGRAM_BOT_TOKEN', ''):
        return None
    try:
        photos = requests.get(
            _api_url('getUserProfilePhotos'),
            params={'user_id': telegram_user_id, 'limit': 1}, timeout=10,
        ).json()
        sizes = ((photos.get('result') or {}).get('photos') or [[]])[0]
        if not photos.get('ok') or not sizes:
            return None
        # Sizes are ascending; take the largest that is still avatar-sized.
        fitting = [size for size in sizes if max(size.get('width', 0), size.get('height', 0)) <= 800]
        file_id = (fitting or sizes)[-1]['file_id']
        info = requests.get(_api_url('getFile'), params={'file_id': file_id}, timeout=10).json()
        file_path = (info.get('result') or {}).get('file_path')
        if not info.get('ok') or not file_path:
            return None
        response = requests.get(
            'https://api.telegram.org/file/bot{}/{}'.format(settings.TELEGRAM_BOT_TOKEN, file_path),
            timeout=15,
        )
        response.raise_for_status()
        return response.content
    except Exception as error:
        logger.warning('Telegram avatar fetch failed: %s', _safe_error_text(error))
        return None


# --- Applying a choice -----------------------------------------------------

class AvatarError(Exception):
    pass


def _set_avatar_url(profile, url):
    previous = profile.avatar_url
    profile.avatar_url = url
    profile.save(update_fields=['avatar_url'])
    if previous != url:
        delete_uploaded(previous)


def use_source(profile, key):
    source = next((s for s in provider_sources(profile.user, profile) if s.key == key), None)
    if source is None:
        raise AvatarError('Этот аккаунт не подключён или в нём нет фото.')
    if source.url:
        _set_avatar_url(profile, source.url)
        return
    data = fetch_telegram_photo(profile.telegram_user_id)
    if not data:
        raise AvatarError(
            'Telegram не отдал фото. Проверьте, что оно есть и видно всем '
            '(Настройки → Конфиденциальность → Фотография профиля).'
        )
    try:
        _set_avatar_url(profile, store_image(data))
    except ValidationError as exc:
        raise AvatarError(exc.messages[0]) from exc


def use_upload(profile, uploaded_file):
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        raise AvatarError('Файл больше 8 МБ.')
    try:
        _set_avatar_url(profile, store_image(uploaded_file.read()))
    except ValidationError as exc:
        raise AvatarError(exc.messages[0]) from exc


def remove(profile):
    _set_avatar_url(profile, '')
