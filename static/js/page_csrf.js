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

  function pageToken(doc) {
    doc = doc || global.document;
    if (!doc || typeof doc.getElementById !== 'function') return '';
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
