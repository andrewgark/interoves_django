import logging
import os

from django.conf import settings
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
django_asgi_app = get_asgi_application()

# Project imports need the app registry populated by get_asgi_application().
from games.ops.cron.runtime import RUNTIME_ROLE_WEB, runtime_role  # noqa: E402
from games.realtime.admission import DatabaseAdmissionASGIMiddleware  # noqa: E402


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
        # One of the web permits is kept for players' moves (answers, hints,
        # timing); page reads use the others.
        priority_reserved = int(os.environ.get(
            'DB_ADMISSION_PRIORITY_RESERVED', '1' if web_defaults and max_active >= 2 else '0',
        ))
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
        priority_reserved=priority_reserved,
    )


http_asgi_app = _http_application_with_admission(django_asgi_app)

import django  # noqa: E402
from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.urls import re_path  # noqa: E402

from games.realtime.track_compat import TrackGame, UserTrackConsumer  # noqa: E402
from interoves_django.asgi_event_loop_monitor import EventLoopLagMonitorMiddleware  # noqa: E402

django.setup()


def _warm_censorly_lexicon():
    """Build the Censorly root bank before the process takes traffic.

    Built lazily, it cost the first matching guess of every web process
    5-15 s of CPU, stalling every other request the process was serving.
    """
    if runtime_role() != RUNTIME_ROLE_WEB or os.environ.get('CENSORLY_WARM_LEXICON', '1') == '0':
        return
    try:
        from games.puzzles.censorly.lexical.deciders.rootbank import ROOT_BANK

        ROOT_BANK.built()
    except Exception:
        logging.getLogger(__name__).exception('censorly lexicon warm-up failed')


_warm_censorly_lexicon()

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
