/**
 * Interoves offline Service Worker (docs/offline-play-plan.md, phase 2).
 *
 * Served from /offline-sw.js by games/site/views/pages/offline_sw.py, which
 * fills in the deploy version. Pages register it only when the player may
 * play offline.
 *
 * - Navigations go to the network first. Only when it fails is a page served
 *   from the cache, and only a page that asked to be kept (the ladder page
 *   puts itself into PAGES_CACHE). Kept pages are refreshed on every online
 *   visit.
 * - /static/ assets are cache-first: their URLs carry ?v=<deploy>, and the
 *   cache of an older deploy is dropped on activation.
 * - POSTs, WebSockets and other origins are never touched.
 */
'use strict';

var VERSION = '__DEPLOY_VERSION__';
var PAGES_CACHE = 'interoves-offline-pages-v1';
var STATIC_CACHE_PREFIX = 'interoves-offline-static-';
var STATIC_CACHE = STATIC_CACHE_PREFIX + VERSION;

self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (key) {
        return key.indexOf(STATIC_CACHE_PREFIX) === 0 && key !== STATIC_CACHE;
      }).map(function (key) { return caches.delete(key); }));
    }).then(function () { return self.clients.claim(); })
  );
});

function navigation(request) {
  return fetch(request).then(function (response) {
    if (response.ok) {
      var copy = response.clone();
      caches.open(PAGES_CACHE).then(function (cache) {
        return cache.match(request).then(function (kept) {
          if (kept) return cache.put(request, copy);
          return null;
        });
      }).catch(function () {});
    }
    return response;
  }, function () {
    // The page was kept by a fetch() from the page, the request now is a
    // navigation: their headers differ, so Vary (Cookie, Origin) must not stop
    // the match.
    return caches.open(PAGES_CACHE)
      .then(function (cache) { return cache.match(request, { ignoreVary: true }); })
      .then(function (kept) { return kept || Response.error(); });
  });
}

function staticAsset(request) {
  return caches.open(STATIC_CACHE).then(function (cache) {
    return cache.match(request).then(function (hit) {
      if (hit) return hit;
      return fetch(request).then(function (response) {
        if (response.ok) cache.put(request, response.clone());
        return response;
      });
    });
  });
}

self.addEventListener('fetch', function (event) {
  var request = event.request;
  if (request.method !== 'GET') return;
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === 'navigate') {
    event.respondWith(navigation(request));
  } else if (url.pathname.indexOf('/static/') === 0) {
    event.respondWith(staticAsset(request));
  }
});
