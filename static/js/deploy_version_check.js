/**
 * When SITE_DEPLOY_VERSION changes, syncs localStorage and redirects once with a
 * cache-busting query param so the browser fetches a fresh HTML document.
 * Set SITE_DEPLOY_VERSION on each deploy (EB env). Empty version disables checks.
 *
 * Note: this is the strongest refresh we can do from JS; it is not identical to
 * Ctrl+F5 for every cached subresource, but usually fixes stale HTML/entry JS.
 */
(function () {
  'use strict';

  var KEY = 'interoves_deploy_v';
  var URL = '/meta/deploy-version/';

  function stripCacheBustQuery(search) {
    if (!search || search === '?') {
      return '';
    }
    var q = search.charAt(0) === '?' ? search.slice(1) : search;
    var parts = q.split('&').filter(function (p) {
      return p && p.indexOf('_interoves_cb=') !== 0;
    });
    return parts.length ? '?' + parts.join('&') : '';
  }

  function hardNavigate() {
    var path = window.location.pathname + stripCacheBustQuery(window.location.search);
    var sep = path.indexOf('?') >= 0 ? '&' : '?';
    window.location.replace(
      path + sep + '_interoves_cb=' + Date.now() + (window.location.hash || '')
    );
  }

  function run() {
    fetch(URL, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var serverV = data && data.version != null ? String(data.version).trim() : '';
        if (!serverV) {
          return;
        }
        var storedV;
        try {
          storedV = localStorage.getItem(KEY);
        } catch (e) {
          return;
        }
        if (storedV === null || storedV === '') {
          try {
            localStorage.setItem(KEY, serverV);
          } catch (e2) {}
          return;
        }
        if (storedV === serverV) {
          return;
        }
        var persisted = false;
        try {
          localStorage.setItem(KEY, serverV);
          persisted = true;
        } catch (e3) {}
        if (!persisted) {
          try {
            if (sessionStorage.getItem('interoves_deploy_rl') === serverV) {
              return;
            }
            sessionStorage.setItem('interoves_deploy_rl', serverV);
          } catch (e4) {}
        }
        hardNavigate();
      })
      .catch(function () {});
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }
})();
