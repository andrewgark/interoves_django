import os
from django.core.asgi import get_asgi_application
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
django_asgi_app = get_asgi_application()

import django
# from channels.http import AsgiHandler
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from django.urls import re_path
from django.core.asgi import get_asgi_application
from games.views.track import TrackGame


django.setup()

application = ProtocolTypeRouter({
  "http":  django_asgi_app,
  "websocket": AuthMiddlewareStack(
        URLRouter([
            re_path(r'^games/(?P<game_id>[a-zA-Z0-9_]+)/track/?$', TrackGame.as_asgi()),
        ])
    )
})
