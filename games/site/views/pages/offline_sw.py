"""The offline Service Worker, served from the site root so it may control every page."""

from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.http import require_GET

SW_SOURCE = Path(settings.BASE_DIR) / 'static' / 'js' / 'offline_sw.js'

# With offline play switched off, an installed worker removes itself and its
# caches: the kill switch reaches browsers that registered it earlier.
UNREGISTER_SOURCE = """'use strict';
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (key) {
      return key.indexOf('interoves-offline-') === 0;
    }).map(function (key) { return caches.delete(key); }));
  }).then(function () { return self.registration.unregister(); }));
});
"""


@lru_cache(maxsize=1)
def _worker_source(deploy_version):
    return SW_SOURCE.read_text(encoding='utf-8').replace('__DEPLOY_VERSION__', deploy_version)


@require_GET
def offline_service_worker(request):
    if settings.OFFLINE_PLAY_ENABLED:
        # Same value as the site_deploy_version context processor (with its
        # '0' default in templates), so page and worker name the same caches.
        version = str(getattr(settings, 'SITE_DEPLOY_VERSION', '') or '').strip() or '0'
        body = _worker_source(version)
    else:
        body = UNREGISTER_SOURCE
    response = HttpResponse(body, content_type='application/javascript; charset=utf-8')
    # The browser checks for a new worker on navigations; never serve a stale one.
    response['Cache-Control'] = 'no-cache'
    return response
