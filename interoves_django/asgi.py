import logging
import os
from django.conf import settings
from django.core.asgi import get_asgi_application
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
django_asgi_app = get_asgi_application()

from games.asgi_admission import DatabaseAdmissionASGIMiddleware
from games.runtime import RUNTIME_ROLE_WEB, runtime_role


def _http_application_with_admission(app):
    """Bound web request concurrency while retaining env-based overrides."""
    # Budget: up to three web hosts during an additional-batch rollout, four
    # admitted requests per Daphne (12); up to 26 ECS DB contexts during a
    # simultaneous worker rollout, 9 for the retained legacy integrations
    # consumer, and 15 reserved for operators/other jobs. Total 62/80 leaves
    # 18 connections outside the modeled budget. These are conservative
    # ceilings, not a claim that every request always opens a connection.
    web_defaults = runtime_role() == RUNTIME_ROLE_WEB
    default_cap = '4' if web_defaults else '0'
    default_waiters = '8' if web_defaults else '0'
    default_wait_timeout = '1' if web_defaults else '0'
    try:
        max_active = int(os.environ.get('DB_ADMISSION_MAX_ACTIVE', default_cap))
        max_waiters = int(os.environ.get('DB_ADMISSION_MAX_WAITERS', default_waiters))
        wait_timeout = float(os.environ.get('DB_ADMISSION_WAIT_TIMEOUT_SECONDS', default_wait_timeout))
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
from games.realtime.track_compat import TrackGame, UserTrackConsumer
from interoves_django.asgi_event_loop_monitor import EventLoopLagMonitorMiddleware


django.setup()

application = EventLoopLagMonitorMiddleware(ProtocolTypeRouter({
  "http":  http_asgi_app,
  "websocket": AuthMiddlewareStack(
        URLRouter([
            re_path(r'^ws/track/?$', UserTrackConsumer.as_asgi()),
            re_path(r'^(?P<project_id>[a-zA-Z0-9_-]+)/games/(?P<game_id>[a-zA-Z0-9_]+)/track/?$', TrackGame.as_asgi()),
            re_path(r'^games/(?P<game_id>[a-zA-Z0-9_]+)/track/?$', TrackGame.as_asgi()),
        ])
    )
}))

logging.getLogger("daphne").info(
    "asgi_process_start pid=%d ppid=%d deploy_version=%s",
    os.getpid(),
    os.getppid(),
    getattr(settings, "SITE_DEPLOY_VERSION", "unknown"),
)
