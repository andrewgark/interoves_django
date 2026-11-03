/*
 * Bell in the header (site/base.html): badge, dropdown, «прочитать все».
 * The dropdown HTML comes from /notifications/dropdown/, which also clears
 * the badge on the server. New notifications arrive over the track socket
 * as `interoves:notification` (track_ws.js) with {badge}.
 */
(function () {
  'use strict';

  var root = document.querySelector('[data-bell]');
  if (!root) return;
  var toggle = root.querySelector('[data-bell-toggle]');
  var panel = root.querySelector('[data-bell-panel]');
  var count = root.querySelector('[data-bell-count]');
  var loading = false;

  function csrfToken() {
    var match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function setBadge(n) {
    n = Number(n) || 0;
    count.hidden = n <= 0;
    count.textContent = n > 9 ? '9+' : String(n);
    toggle.setAttribute('aria-label', n > 0 ? 'Уведомления: ' + n + ' новых' : 'Уведомления');
  }

  function load() {
    if (loading) return;
    loading = true;
    panel.setAttribute('aria-busy', 'true');
    fetch(root.getAttribute('data-src'), { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
      .then(function (html) {
        panel.innerHTML = html;
        setBadge(0);
      })
      .catch(function () {
        panel.innerHTML = '<p class="new-notif-empty">Не удалось загрузить. Попробуйте ещё раз.</p>';
      })
      .then(function () {
        loading = false;
        panel.removeAttribute('aria-busy');
      });
  }

  function open() {
    panel.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    load();
  }

  function close(focus) {
    if (panel.hidden) return;
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    if (focus) toggle.focus();
  }

  toggle.addEventListener('click', function (e) {
    e.stopPropagation();
    if (panel.hidden) open(); else close(false);
  });

  panel.addEventListener('click', function (e) {
    var readAll = e.target.closest('[data-bell-read-all]');
    if (!readAll) return;
    e.preventDefault();
    readAll.disabled = true;
    fetch(root.getAttribute('data-read-all-url'), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrfToken(), 'X-Requested-With': 'XMLHttpRequest', Accept: 'application/json' },
    }).then(function () {
      panel.querySelectorAll('.new-notif.is-unread').forEach(function (item) {
        item.classList.remove('is-unread');
        var dot = item.querySelector('.new-notif__dot');
        if (dot) dot.remove();
      });
      readAll.remove();
    }).catch(function () { readAll.disabled = false; });
  });

  document.addEventListener('click', function (e) {
    if (!root.contains(e.target)) close(false);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') close(true);
  });

  document.addEventListener('interoves:notification', function (e) {
    var detail = (e && e.detail) || {};
    if (!panel.hidden) {
      load();
      return;
    }
    if (detail.badge != null) setBadge(detail.badge);
  });
})();
