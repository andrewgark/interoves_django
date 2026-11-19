/**
 * Page-owned CSRF token for the new UI.
 *
 * Live task HTML is rendered for the teammate who just mutated state and then
 * applied on every open team page. That fragment includes their
 * csrfmiddlewaretoken. Django prefers the POST field over X-CSRFToken, so the
 * next fetch() from the other member becomes a 403 HTML page and the UI shows
 * «Ошибка сети». Always stamp the token from #interoves-page-csrf, which is
 * never replaced by live updates.
 *
 * Local check: node static/js/page_csrf.test.js
 */
(function (global) {
  'use strict';

  var FORM_ID = 'interoves-page-csrf';
  var FIELD = 'csrfmiddlewaretoken';

  // The live csrftoken cookie wins over the token rendered into the page:
  // when the browser's cookie changes (another tab, sign-in/out, Safari
  // dropping cookies) a page left open kept sending its old token and every
  // POST failed with 403 until reload (2026-11-19: one iOS player's Salad
  // answers and timer for an hour). Django accepts the cookie's secret.
  function cookieToken(doc) {
    var raw = doc && typeof doc.cookie === 'string' ? doc.cookie : '';
    var m = raw.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (!m) return '';
    try { return decodeURIComponent(m[1]); } catch (e) { return m[1]; }
  }

  function pageToken(doc) {
    doc = doc || global.document;
    if (!doc) return '';
    var fromCookie = cookieToken(doc);
    if (fromCookie) return fromCookie;
    if (typeof doc.getElementById !== 'function') return '';
    var form = doc.getElementById(FORM_ID);
    var el = form ? form.querySelector('input[name="' + FIELD + '"]') : null;
    return el && el.value ? String(el.value) : '';
  }

  function stampFormData(fd, token) {
    token = token == null ? pageToken() : token;
    if (fd && token) fd.set(FIELD, token);
    return fd;
  }

  function stampParams(params, token) {
    token = token == null ? pageToken() : token;
    if (params && token) params.set(FIELD, token);
    return params;
  }

  function stampRoot(root, token) {
    token = token == null ? pageToken() : token;
    if (!root || !token || typeof root.querySelectorAll !== 'function') return root;
    root.querySelectorAll('input[name="' + FIELD + '"]').forEach(function (inp) {
      inp.value = token;
    });
    return root;
  }

  global.InterovesPageCsrf = {
    FORM_ID: FORM_ID,
    FIELD: FIELD,
    pageToken: pageToken,
    stampFormData: stampFormData,
    stampParams: stampParams,
    stampRoot: stampRoot,
  };
})(typeof window !== 'undefined' ? window : global);
