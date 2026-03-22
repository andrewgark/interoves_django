/**
 * When SITE_DEPLOY_VERSION changes, syncs localStorage and redirects once with a
 * cache-busting query param so the browser fetches a fresh HTML document.
 * Set SITE_DEPLOY_VERSION on each deploy (EB env). Empty version disables checks.
 *
 * Compares the version embedded in this HTML (data-site-deploy-version / #interoves-site-deploy)
 * with GET /meta/deploy-version/ so a cached document from an older build still triggers a reload
 * even when localStorage was never set.
 *
 * Note: this is the strongest refresh we can do from JS; it is not identical to
 * Ctrl+F5 for every cached subresource, but usually fixes stale HTML/entry JS.
 */
(function () {
  'use strict';

  var KEY = 'interoves_deploy_v';
  var URL = '/meta/deploy-version/';

  function embeddedVersion() {
    var h = document.documentElement.getAttribute('data-site-deploy-version');
    if (h != null && h !== '') {
      return String(h).trim();
    }
    var meta = document.querySelector('meta[name="interoves-deploy-version"]');
    if (meta) {
      var mc = meta.getAttribute('content');
      if (mc != null && mc !== '') {
        return String(mc).trim();
      }
    }
    var el = document.getElementById('interoves-site-deploy');
    if (el) {
      return String(el.getAttribute('data-v') || '').trim();
    }
    return '';
  }

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
      headers: {
        Accept: 'application/json',
        'Cache-Control': 'no-cache',
        Pragma: 'no-cache',
      },
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var serverV = data && data.version != null ? String(data.version).trim() : '';
        if (!serverV) {
          return;
        }
        var embeddedV = embeddedVersion();
        var storedV;
        try {
          storedV = localStorage.getItem(KEY);
        } catch (e) {
          return;
        }

        // Cached HTML from an older build: embed disagrees with live version
        if (embeddedV && serverV && embeddedV !== serverV) {
          var ok1 = false;
          try {
            localStorage.setItem(KEY, serverV);
            ok1 = true;
          } catch (e2) {}
          if (!ok1) {
            try {
              if (sessionStorage.getItem('interoves_deploy_rl') === serverV) {
                return;
              }
              sessionStorage.setItem('interoves_deploy_rl', serverV);
            } catch (e4) {}
          }
          hardNavigate();
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

        // Fresh document: embedded version matches server — only storage was stale
        if (embeddedV && embeddedV === serverV) {
          return;
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

  // Mobile Safari restores from bfcache without re-running scripts; re-check deploy id.
  window.addEventListener('pageshow', function (ev) {
    if (ev.persisted) {
      run();
    }
  });
})();
