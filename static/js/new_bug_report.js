(function () {
  'use strict';

  var modal = document.getElementById('new-bug-report-modal');
  if (!modal) return;

  var textEl = document.getElementById('new-bug-report-text');
  var labelEl = document.getElementById('new-bug-report-task-label');
  var errorEl = document.getElementById('new-bug-report-error');
  var successEl = document.getElementById('new-bug-report-success');
  var submitEl = modal.querySelector('[data-bug-report-submit]');
  var currentTaskId = null;
  var submitting = false;

  function csrf() {
    if (window.InterovesPageCsrf) return window.InterovesPageCsrf.pageToken();
    var form = document.querySelector('#interoves-page-csrf input[name=csrfmiddlewaretoken]') ||
      document.querySelector('input[name=csrfmiddlewaretoken]');
    if (form && form.value) return form.value;
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function rootData() {
    return document.getElementById('censorly-root') ||
      document.getElementById('alphabetty-root') ||
      document.body;
  }

  function anonKey(root) {
    if (root && root.getAttribute('data-authenticated') === '1') return '';
    var key = root && root.getAttribute('data-anon-key');
    if (key) return key;
    try {
      var match = document.cookie.match(/(?:^|;\s*)interoves_anon=([^;]+)/);
      if (match) return decodeURIComponent(match[1]);
      return localStorage.getItem('interoves_anon_key') || '';
    } catch (e) {
      return '';
    }
  }

  function endpoint() {
    var template = modal.getAttribute('data-bug-report-url-template') || '';
    return template.replace('/0/', '/' + encodeURIComponent(currentTaskId) + '/');
  }

  function reset() {
    textEl.value = '';
    errorEl.textContent = '';
    errorEl.hidden = true;
    successEl.hidden = true;
    submitEl.hidden = false;
    submitEl.disabled = false;
    submitting = false;
  }

  function open(button) {
    currentTaskId = button.getAttribute('data-task-id') || '';
    reset();
    labelEl.textContent = button.getAttribute('data-task-label') || ('#' + currentTaskId);
    modal.hidden = false;
    modal.classList.add('is-open');
    document.body.classList.add('new-modal-open');
    textEl.focus();
  }

  function close() {
    if (submitting) return;
    modal.hidden = true;
    modal.classList.remove('is-open');
    document.body.classList.remove('new-modal-open');
    currentTaskId = null;
  }

  function showError(message, response) {
    var requestId = response && response.headers && response.headers.get('X-Request-ID');
    var suffix = response && response.status ? ' (HTTP ' + response.status + ')' : '';
    errorEl.textContent = message + suffix + (requestId ? ' · Код: ' + requestId : '');
    errorEl.hidden = false;
  }

  function post() {
    var root = rootData();
    var body = new URLSearchParams();
    body.set('text', textEl.value.trim());
    body.set('game_id', modal.getAttribute('data-bug-report-game-id') || root.getAttribute('data-game-id') || '');
    body.set('anon_key', anonKey(root));
    var shareHash = document.body.getAttribute('data-offer-share-hash');
    if (shareHash) body.set('offer_share', shareHash);
    return fetch(endpoint(), {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': csrf(),
      },
      body: body.toString(),
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok || data.ok === false) {
          var error = new Error(data.error || 'Не удалось отправить репорт.');
          error.response = response;
          throw error;
        }
        return data;
      });
    });
  }

  document.addEventListener('click', function (event) {
    var openButton = event.target.closest && event.target.closest('[data-bug-report-open]');
    if (openButton) {
      event.preventDefault();
      open(openButton);
      return;
    }
    if (event.target.closest && event.target.closest('[data-bug-report-close]')) {
      event.preventDefault();
      close();
      return;
    }
    if (!submitEl || !event.target.closest || !event.target.closest('[data-bug-report-submit]')) return;
    event.preventDefault();
    event.stopPropagation();
    if (submitting || !currentTaskId) return;
    if (!textEl.value.trim()) {
      showError('Опишите проблему.');
      textEl.focus();
      return;
    }
    submitting = true;
    submitEl.disabled = true;
    errorEl.hidden = true;
    post().then(function (data) {
      var link = document.getElementById('new-bug-report-success-link');
      if (link && data.report_url) link.setAttribute('href', data.report_url);
      successEl.hidden = false;
      submitEl.hidden = true;
    }).catch(function (error) {
      showError(error.message || 'Не удалось отправить репорт.', error.response);
    }).finally(function () {
      submitting = false;
      if (!submitEl.hidden) submitEl.disabled = false;
    });
  });

  modal.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') close();
  });
})();
