/**
 * ▲▼ votes on tasks: [data-like-btn] / [data-dislike-btn] in .new-vote.
 *
 * One delegated handler for every page. A page that shows votes calls
 *   InterovesTaskVotes.configure({url, gameId, getAnonKey})
 * where ``url`` is the like_dislike URL for task 0. Every copy of a task's
 * vote (the meta bar, «Оцените игру» under the result) updates together.
 *
 * Ladder clue likes are a different endpoint and stay in task_group_page.js.
 */
(function () {
  'use strict';
  if (window.InterovesTaskVotes) return;

  var config = null;
  var inFlight = Object.create(null);
  var statusTimers = Object.create(null);

  function all(selector, fn) {
    document.querySelectorAll(selector).forEach(fn);
  }
  function buttons(taskId) {
    return '[data-like-btn][data-task-id="' + taskId + '"], ' +
      '[data-dislike-btn][data-task-id="' + taskId + '"]';
  }

  function csrf() {
    if (window.InterovesPageCsrf) return window.InterovesPageCsrf.pageToken();
    var el = document.querySelector('#interoves-page-csrf input[name=csrfmiddlewaretoken]') ||
      document.querySelector('input[name=csrfmiddlewaretoken]');
    return el ? el.value : '';
  }

  // Server-rendered data-* → .is-active (after load and after swapped HTML).
  function sync(scope) {
    var root = scope || document;
    root.querySelectorAll('[data-like-btn]').forEach(function (btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-liked') === '1');
    });
    root.querySelectorAll('[data-dislike-btn]').forEach(function (btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-disliked') === '1');
    });
    if (window.InterovesVoteStack) window.InterovesVoteStack.refresh(scope);
  }

  function setBusy(taskId, busy) {
    all(buttons(taskId), function (btn) {
      btn.disabled = busy;
      if (busy) btn.setAttribute('aria-busy', 'true');
      else btn.removeAttribute('aria-busy');
    });
  }

  function clearError(taskId) {
    clearTimeout(statusTimers[taskId]);
    all('[data-like-status="' + taskId + '"]', function (status) {
      status.hidden = true;
      status.textContent = '';
    });
  }

  function showError(taskId) {
    clearError(taskId);
    all('[data-like-status="' + taskId + '"]', function (status) {
      status.textContent = 'Не удалось сохранить. Попробуйте ещё раз.';
      status.hidden = false;
    });
    statusTimers[taskId] = setTimeout(function () { clearError(taskId); }, 5000);
  }

  function apply(taskId, data) {
    all('[data-like-count="' + taskId + '"]', function (count) {
      if (typeof data.likes !== 'undefined') count.textContent = data.likes;
    });
    all('[data-dislike-count="' + taskId + '"]', function (count) {
      if (typeof data.dislikes !== 'undefined') count.textContent = data.dislikes;
    });
    all('[data-like-btn][data-task-id="' + taskId + '"]', function (btn) {
      if (typeof data.liked === 'undefined') return;
      btn.setAttribute('data-liked', data.liked ? '1' : '0');
      btn.classList.toggle('is-active', !!data.liked);
    });
    all('[data-dislike-btn][data-task-id="' + taskId + '"]', function (btn) {
      if (typeof data.disliked === 'undefined') return;
      btn.setAttribute('data-disliked', data.disliked ? '1' : '0');
      btn.classList.toggle('is-active', !!data.disliked);
    });
    if (window.InterovesVoteStack) window.InterovesVoteStack.refresh();
  }

  function post(taskId, likes, dislikes) {
    var body = new URLSearchParams();
    body.set('likes', String(likes));
    body.set('dislikes', String(dislikes));
    body.set('game_id', config.gameId || '');
    body.set('anon_key', (config.getAnonKey && config.getAnonKey()) || '');
    var offerShare = document.body.getAttribute('data-offer-share-hash');
    if (offerShare) body.set('offer_share', offerShare);
    return fetch(config.url.replace('/0/', '/' + taskId + '/'), {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': csrf(),
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
      },
      body: body.toString(),
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function submit(taskId, likes, dislikes) {
    if (inFlight[taskId]) return;
    inFlight[taskId] = true;
    clearError(taskId);
    setBusy(taskId, true);
    post(taskId, likes, dislikes).then(function (data) {
      apply(taskId, data);
    }).catch(function () {
      showError(taskId);
    }).then(function () {
      delete inFlight[taskId];
      setBusy(taskId, false);
    });
  }

  document.addEventListener('click', function (e) {
    if (!config || !e.target || !e.target.closest) return;
    var like = e.target.closest('[data-like-btn]');
    var dislike = like ? null : e.target.closest('[data-dislike-btn]');
    var btn = like || dislike;
    if (!btn) return;
    e.preventDefault();
    var active = btn.getAttribute(like ? 'data-liked' : 'data-disliked') === '1';
    var delta = active ? -1 : 1;
    submit(btn.getAttribute('data-task-id'), like ? delta : 0, like ? 0 : delta);
  }, true);

  window.InterovesTaskVotes = {
    configure: function (options) {
      config = options;
      sync();
    },
    sync: sync,
    apply: apply,
  };
})();
