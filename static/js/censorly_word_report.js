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
    otherWrap.hidden = !needed;
    otherEl.required = needed;
    otherLabel.textContent = reason === 'FALSE_POSITIVE'
      ? 'Каким словом оно было открыто?'
      : 'Какое слово должно было его открыть?';
  }

  function close() {
    if (submitting) return;
    modal.hidden = true;
    modal.classList.remove('is-open');
    selectedToken = null;
  }

  function open(token) {
    selectedToken = token;
    targetEl.textContent = token.dataset.surface || token.textContent.trim();
    modal.querySelectorAll('input[name=censorly-word-report-reason]').forEach(function (input) {
      input.checked = false;
    });
    otherEl.value = '';
    commentEl.value = '';
    errorEl.hidden = true;
    successEl.hidden = true;
    submitEl.hidden = false;
    modal.querySelectorAll('input, textarea').forEach(function (input) { input.disabled = false; });
    otherWrap.hidden = true;
    otherEl.required = false;
    modal.hidden = false;
    modal.classList.add('is-open');
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
  });

  submitEl.addEventListener('click', function () {
    if (submitting || !selectedToken) return;
    var reason = selectedReason();
    var other = (otherEl.value || '').trim();
    if (!reason) {
      errorEl.textContent = 'Выберите причину жалобы.';
      errorEl.hidden = false;
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
      },
      body: JSON.stringify(body),
    }).then(function (response) {
      return response.json().then(function (data) {
        return { ok: response.ok && data.ok !== false, data: data };
      });
    }).then(function (result) {
      if (!result.ok) {
        errorEl.textContent = (result.data && result.data.error) || 'Не удалось отправить жалобу.';
        errorEl.hidden = false;
        return;
      }
      errorEl.hidden = true;
      successEl.hidden = false;
      submitEl.hidden = true;
      modal.querySelectorAll('input, textarea').forEach(function (input) { input.disabled = true; });
    }).catch(function () {
      errorEl.textContent = 'Сеть недоступна.';
      errorEl.hidden = false;
    }).finally(function () {
      submitting = false;
      submitEl.disabled = false;
    });
  });
})();
