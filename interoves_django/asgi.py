import logging
import os
from django.conf import settings
from django.core.asgi import get_asgi_application
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
django_asgi_app = get_asgi_application()

from games.asgi_admission import DatabaseAdmissionASGIMiddleware


def _http_application_with_admission(app):
    """Enable a per-process request cap only when explicitly configured."""
    try:
        max_active = int(os.environ.get('DB_ADMISSION_MAX_ACTIVE', '0'))
        max_waiters = int(os.environ.get('DB_ADMISSION_MAX_WAITERS', '0'))
        wait_timeout = float(os.environ.get('DB_ADMISSION_WAIT_TIMEOUT_SECONDS', '0'))
        retry_after = int(os.environ.get('DB_ADMISSION_RETRY_AFTER_SECONDS', '2'))
    except ValueError as exc:
        raise RuntimeError('Invalid DB admission configuration.') from exc
    if max_active < 0:
        raise RuntimeError('DB_ADMISSION_MAX_ACTIVE must be zero or positive.')
    return DatabaseAdmissionASGIMiddleware(
        app,
        max_active=max_active,
        max_waiters=max_waiters,
        wait_timeout=wait_timeout,
        retry_after_seconds=retry_after,
    )


http_asgi_app = _http_application_with_admission(django_asgi_app)

import django
# from channels.http import AsgiHandler
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from django.urls import re_path
from django.core.asgi import get_asgi_application
from games.views.track import TrackGame, UserTrackConsumer


django.setup()

application = ProtocolTypeRouter({
  "http":  http_asgi_app,
  "websocket": AuthMiddlewareStack(
        URLRouter([
            re_path(r'^ws/track/?$', UserTrackConsumer.as_asgi()),
            re_path(r'^(?P<project_id>[a-zA-Z0-9_-]+)/games/(?P<game_id>[a-zA-Z0-9_]+)/track/?$', TrackGame.as_asgi()),
            re_path(r'^games/(?P<game_id>[a-zA-Z0-9_]+)/track/?$', TrackGame.as_asgi()),
        ])
    )
})

logging.getLogger("daphne").info(
    "asgi_process_start pid=%d ppid=%d deploy_version=%s",
    os.getpid(),
    os.getppid(),
    getattr(settings, "SITE_DEPLOY_VERSION", "unknown"),
)
