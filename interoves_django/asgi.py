import os
from django.core.asgi import get_asgi_application
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
django_asgi_app = get_asgi_application()

import django
# from channels.http import AsgiHandler
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from django.urls import re_path, path
from django.core.asgi import get_asgi_application
from games.views.track.track_game import TrackGame
from games.views.track.track_project import TrackProject
from interoves_django.projects import PROJECTS


django.setup()


asgi_urls = [
    re_path(r'^games/(?P<game_id>[a-zA-Z0-9_]+)/track/?$', TrackGame.as_asgi()),
]
for project in PROJECTS:
    asgi_urls.append(path(f'{project}/', TrackProject.as_asgi()))


application = ProtocolTypeRouter({
  "http":  django_asgi_app,
  "websocket": AuthMiddlewareStack(
        URLRouter(asgi_urls)
    )
})
