(function () {
  'use strict';

  var root = document.getElementById('censorly-root');
  var modal = document.getElementById('censorly-word-report-modal');
  if (!root || !modal) return;

  var targetEl = document.getElementById('censorly-word-report-target');
  var otherWrap = document.getElementById('censorly-word-report-other-wrap');
  var otherLabel = document.getElementById('censorly-word-report-other-label');
  var otherEl = document.getElementById('censorly-word-report-other');
  var commentEl = document.getElementById('censorly-word-report-comment');
  var errorEl = document.getElementById('censorly-word-report-error');
  var successEl = document.getElementById('censorly-word-report-success');
  var submitEl = modal.querySelector('[data-censorly-word-report-submit]');
  var selectedToken = null;
  var previousFocus = null;
  var submitting = false;

  function csrf() {
    if (window.InterovesPageCsrf) return window.InterovesPageCsrf.pageToken();
    var form = document.querySelector('#censorly-form input[name=csrfmiddlewaretoken]');
    if (form) return form.value;
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function selectedReason() {
    var input = modal.querySelector('input[name=censorly-word-report-reason]:checked');
    return input ? input.value : '';
  }

  function updateOtherField() {
    var reason = selectedReason();
    var needed = reason === 'FALSE_POSITIVE' || reason === 'FALSE_NEGATIVE';
    var knownOpeningGuess = reason === 'FALSE_POSITIVE' && selectedToken
      ? (selectedToken.dataset.openedByGuess || '') : '';
    errorEl.hidden = true;
    otherWrap.hidden = !needed;
    otherEl.readOnly = !!knownOpeningGuess;
    otherEl.required = needed && !knownOpeningGuess;
    if (knownOpeningGuess) otherEl.value = knownOpeningGuess;
    else if (!needed || reason === 'FALSE_NEGATIVE') otherEl.value = '';
    otherLabel.textContent = reason === 'FALSE_POSITIVE'
      ? 'Каким словом оно было открыто?'
      : 'Какое слово должно было его открыть?';
  }

  function showNetworkError(message, response) {
    var requestId = response && response.headers && response.headers.get('X-Request-ID');
    var suffix = response && response.status ? ' (HTTP ' + response.status + ')' : '';
    errorEl.textContent = message + suffix + (requestId ? ' · Код: ' + requestId : '');
    errorEl.hidden = false;
  }

  function close() {
    if (submitting) return;
    modal.hidden = true;
    modal.classList.remove('is-open');
    document.body.classList.remove('new-modal-open');
    selectedToken = null;
    if (previousFocus && document.contains(previousFocus)) previousFocus.focus();
    previousFocus = null;
  }

  function open(token) {
    previousFocus = document.activeElement;
    selectedToken = token;
    targetEl.textContent = token.dataset.surface || token.textContent.trim();
    modal.querySelectorAll('input[name=censorly-word-report-reason]').forEach(function (input) {
      input.checked = false;
    });
    otherEl.value = '';
    otherEl.readOnly = false;
    commentEl.value = '';
    errorEl.hidden = true;
    successEl.hidden = true;
    submitEl.hidden = false;
    modal.querySelectorAll('input, textarea').forEach(function (input) { input.disabled = false; });
    otherWrap.hidden = true;
    otherEl.required = false;
    modal.hidden = false;
    modal.classList.add('is-open');
    document.body.classList.add('new-modal-open');
    modal.querySelector('input[name=censorly-word-report-reason]').focus();
  }

  modal.querySelectorAll('input[name=censorly-word-report-reason]').forEach(function (input) {
    input.addEventListener('change', updateOtherField);
  });

  document.addEventListener('click', function (event) {
    var token = event.target.closest && event.target.closest('.censorly-tok--reportable');
    if (token && root.contains(token)) {
      event.preventDefault();
      open(token);
      return;
    }
    if (event.target.closest && event.target.closest('[data-censorly-word-report-close]')) {
      close();
    }
  });

  document.addEventListener('keydown', function (event) {
    var token = event.target.closest && event.target.closest('.censorly-tok--reportable');
    if (token && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      open(token);
    }
    if (event.key === 'Escape' && !modal.hidden) close();
    if (event.key === 'Tab' && !modal.hidden) {
      var focusable = Array.prototype.slice.call(modal.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
      )).filter(function (element) {
        return !element.hidden && element.offsetParent !== null;
      });
      if (!focusable.length) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  if (!submitEl) return;
  submitEl.addEventListener('click', function (event) {
    event.preventDefault();
    event.stopPropagation();
    if (submitting || !selectedToken) return;
    if (!root.dataset.wordReportUrl) {
      errorEl.textContent = 'Не удалось определить адрес жалобы.';
      errorEl.hidden = false;
      return;
    }
    var reason = selectedReason();
    var other = (otherEl.value || '').trim();
    if (!reason) {
      errorEl.textContent = 'Выберите причину жалобы.';
      errorEl.hidden = false;
      var firstReason = modal.querySelector('input[name=censorly-word-report-reason]');
      if (firstReason) firstReason.focus();
      return;
    }
    if ((reason === 'FALSE_POSITIVE' || reason === 'FALSE_NEGATIVE') && !other) {
      errorEl.textContent = 'Укажите второе слово.';
      errorEl.hidden = false;
      otherEl.focus();
      return;
    }
    submitting = true;
    submitEl.disabled = true;
    var body = {
      reason: reason,
      target_surface: selectedToken.dataset.surface || selectedToken.textContent.trim(),
      token_id: Number(selectedToken.dataset.id),
      other_word: other,
      comment: (commentEl.value || '').trim(),
      game_id: root.dataset.gameId || '',
      gameplay_context_token: root.dataset.contextToken || '',
    };
    fetch(root.dataset.wordReportUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf(),
        'X-Requested-With': 'XMLHttpRequest',
        'X-Interoves-Game': root.dataset.gameId || '',
        'X-Interoves-Gameplay-Context': root.dataset.contextToken || '',
      },
      body: JSON.stringify(body),
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        return { ok: response.ok && data.ok !== false, data: data, response: response };
      });
    }).then(function (result) {
      if (!result.ok) {
        showNetworkError(
          (result.data && result.data.error) || 'Не удалось отправить жалобу.',
          result.response
        );
        return;
      }
      errorEl.hidden = true;
      successEl.hidden = false;
      submitEl.hidden = true;
      modal.querySelectorAll('input, textarea').forEach(function (input) { input.disabled = true; });
    }).catch(function (error) {
      showNetworkError(error && error.message === 'Failed to fetch' ? 'Сеть недоступна.' : 'Не удалось отправить жалобу.', error && error.response);
    }).finally(function () {
      submitting = false;
      submitEl.disabled = false;
    });
  });
})();
