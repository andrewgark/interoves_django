'use strict';

/**
 * Active solving timer for official daily games.
 * Local display uses performance.now(); the backend is canonical.
 */
(function (root, factory) {
  var api = factory(root);
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.interovesDailySolveTimer = api;
})(typeof window !== 'undefined' ? window : global, function (root) {
  var HEARTBEAT_MS = 15000;
  var STORAGE_PREFIX = 'interoves_daily_timing_v1:';

  function formatElapsed(ms) {
    var seconds = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
    var hours = Math.floor(seconds / 3600);
    var rem = seconds % 3600;
    var minutes = Math.floor(rem / 60);
    var secs = rem % 60;
    if (hours) return hours + 'ч ' + minutes + 'м ' + secs + 'с';
    if (minutes) return minutes + 'м ' + secs + 'с';
    return secs + 'с';
  }

  function shouldRunLocally(state, visibility) {
    if (!state || state.completed || state.manually_paused) return false;
    if (state.status === 'manually_paused' || state.status === 'completed') return false;
    if (visibility && visibility !== 'visible') return false;
    return !!state.is_authoritative && state.status === 'running';
  }

  function uuid() {
    try {
      if (root.crypto && root.crypto.randomUUID) return root.crypto.randomUUID();
    } catch (e) {}
    return String(Date.now()) + '-' + String(Math.random()).slice(2);
  }

  function nowMs(clock) {
    if (clock && typeof clock.now === 'function') return clock.now();
    if (root.performance && typeof root.performance.now === 'function') return root.performance.now();
    return Date.now();
  }

  function visibilityOf(doc) {
    return (doc && doc.visibilityState) || 'visible';
  }

  function create(options) {
    options = options || {};
    var doc = options.document || (root && root.document);
    var url = options.url || '';
    var getAnonKey = options.getAnonKey || function () { return ''; };
    var getCsrf = options.getCsrf || function () { return ''; };
    var fetchFn = options.fetch || (root && root.fetch && root.fetch.bind(root));
    var storage = options.storage || (root && root.sessionStorage);
    var localStore = options.localStorage || (root && root.localStorage);
    var clock = options.clock || root.performance || { now: function () { return Date.now(); } };
    var bootstrap = options.bootstrap || {};
    var solved = !!options.solved;
    var channelFactory = options.broadcastChannel;
    var heartbeatMs = options.heartbeatMs || HEARTBEAT_MS;
    var onState = options.onState || function () {};

    var rootEl = options.root;
    var displayEl = options.displayEl;
    var pauseBtn = options.pauseBtn;
    var overlay = options.overlay;
    var resumeBtn = options.resumeBtn;
    var overlayTitle = options.overlayTitle;
    var overlayText = options.overlayText;
    var boardEl = options.boardEl;

    var sessionKey = 'session:' + url;
    var seqKey = sessionKey + ':seq';
    var sessionId = '';
    var seq = 0;
    try {
      sessionId = (storage && storage.getItem(sessionKey)) || '';
      seq = parseInt((storage && storage.getItem(seqKey)) || '0', 10) || 0;
    } catch (e) {}
    if (!sessionId) {
      sessionId = uuid();
      seq = 0;
      try { if (storage) storage.setItem(sessionKey, sessionId); } catch (e2) {}
    }
    var committedMs = Number(bootstrap.committed_ms || bootstrap.accumulated_ms || 0) || 0;
    var displayBaseMs = Number(bootstrap.accumulated_ms || committedMs) || 0;
    var status = bootstrap.status || (solved ? 'completed' : 'auto_paused');
    var completed = !!bootstrap.completed || status === 'completed' || solved;
    var manuallyPaused = !!bootstrap.manually_paused || status === 'manually_paused';
    var authoritative = !!bootstrap.is_authoritative;
    var exists = !!bootstrap.exists;
    var runningSince = null;
    var heartbeatTimer = null;
    var raf = null;
    var started = false;
    var destroyed = false;
    var awaitingServer = false;
    var foreignHold = false;
    var channel = null;

    function localKey() {
      return STORAGE_PREFIX + url + ':' + (getAnonKey() || 'user');
    }

    function persistLocal() {
      try {
        if (!localStore) return;
        localStore.setItem(localKey(), JSON.stringify({
          accumulated_ms: displayedMs(),
          status: status,
          manually_paused: manuallyPaused,
          completed: completed,
        }));
      } catch (e) {}
    }

    function displayedMs() {
      if (completed || manuallyPaused || status === 'completed' || status === 'manually_paused') {
        return displayBaseMs;
      }
      if (visibilityOf(doc) !== 'visible' || !authoritative || status !== 'running' || runningSince == null) {
        return displayBaseMs;
      }
      return displayBaseMs + Math.max(0, Math.floor(nowMs(clock) - runningSince));
    }

    function currentState() {
      return {
        status: status,
        accumulated_ms: displayedMs(),
        committed_ms: committedMs,
        is_authoritative: authoritative,
        manually_paused: manuallyPaused,
        completed: completed,
        exists: exists,
      };
    }

    function render() {
      var ms = displayedMs();
      if (displayEl) displayEl.textContent = formatElapsed(ms);
      if (rootEl) {
        rootEl.hidden = false;
        rootEl.classList.toggle('is-paused', manuallyPaused || (!authoritative && !completed && status !== 'running'));
        rootEl.classList.toggle('is-completed', completed);
      }
      if (pauseBtn) pauseBtn.hidden = completed;
      var showOverlay = false;
      if (!completed) {
        if (manuallyPaused) showOverlay = true;
        else if (foreignHold && !authoritative && visibilityOf(doc) === 'visible') showOverlay = true;
        else if (
          !awaitingServer
          && exists
          && started
          && visibilityOf(doc) === 'visible'
          && !authoritative
          && status === 'running'
        ) showOverlay = true;
      }
      var wrap = boardEl && boardEl.closest ? boardEl.closest('.new-daily-solve') : null;
      if (wrap) {
        if (showOverlay) wrap.setAttribute('data-daily-paused', '');
        else wrap.removeAttribute('data-daily-paused');
      }
      if (overlay) {
        overlay.hidden = !showOverlay;
        if (boardEl) {
          if (showOverlay) boardEl.setAttribute('inert', '');
          else boardEl.removeAttribute('inert');
        }
      }
      if (overlayTitle) {
        overlayTitle.textContent = manuallyPaused ? 'Пауза' : 'Игра открыта в другой вкладке';
      }
      if (overlayText) {
        overlayText.textContent = manuallyPaused
          ? 'Время остановлено. Нажмите «Продолжить», чтобы вернуться к заданию.'
          : 'Эта же ежедневная игра открыта в другом окне. Нажмите «Продолжить», чтобы вести время здесь.';
      }
      onState(currentState());
    }

    function applySnapshot(snap, opts) {
      opts = opts || {};
      if (destroyed || !snap) return;
      var incomingRunning = snap.status === 'running' && !snap.completed && !snap.manually_paused;
      if (incomingRunning && manuallyPaused) return;
      if (incomingRunning && visibilityOf(doc) === 'hidden') return;
      exists = snap.exists !== false;
      completed = !!snap.completed || snap.status === 'completed';
      manuallyPaused = !!snap.manually_paused || snap.status === 'manually_paused';
      status = snap.status || status;
      if (typeof snap.committed_ms === 'number') committedMs = snap.committed_ms;
      var incoming = Number(snap.accumulated_ms);
      if (completed && snap.frozen_ms != null) {
        displayBaseMs = Number(snap.frozen_ms) || displayBaseMs;
      } else if (!isNaN(incoming)) {
        // Never jump down: pause/hidden responses can lag behind the local monotonic tick.
        displayBaseMs = Math.max(displayBaseMs, incoming);
      }
      authoritative = !!snap.is_authoritative;
      if (authoritative) foreignHold = false;
      if (authoritative && status === 'running' && !completed && !manuallyPaused) {
        runningSince = nowMs(clock);
      }
      persistLocal();
      render();
      syncTicker();
    }

    function nextSeq() {
      seq += 1;
      try { if (storage) storage.setItem(seqKey, String(seq)); } catch (e) {}
      return seq;
    }

    function currentClaimedMs() {
      if (runningSince == null) return 0;
      return Math.max(0, Math.floor(nowMs(clock) - runningSince));
    }

    function freezeOpenInterval() {
      var claimed = currentClaimedMs();
      displayBaseMs = displayedMs();
      runningSince = null;
      return claimed;
    }

    function post(action, extra, keepalive) {
      extra = extra || {};
      var eventSeq = nextSeq();
      if (options.offline) return Promise.resolve(null);
      var claimed = Object.prototype.hasOwnProperty.call(extra, 'claimed_ms')
        ? Math.max(0, Math.floor(Number(extra.claimed_ms) || 0))
        : currentClaimedMs();
      var body = {
        action: action,
        session_id: sessionId,
        event_id: extra.event_id || uuid(),
        seq: eventSeq,
        claimed_ms: claimed,
        anon_key: getAnonKey() || '',
      };
      var headers = {
        'X-CSRFToken': getCsrf(),
        'X-Requested-With': 'XMLHttpRequest',
      };
      var anon = getAnonKey();
      if (anon) headers['X-Interoves-Anon'] = anon;
      if (!fetchFn) return Promise.resolve(null);
      var init = {
        method: 'POST',
        credentials: 'same-origin',
        keepalive: !!keepalive,
        headers: headers,
      };
      if (keepalive) {
        var params = new URLSearchParams();
        Object.keys(body).forEach(function (k) { params.set(k, String(body[k])); });
        params.set('csrfmiddlewaretoken', getCsrf());
        init.headers['Content-Type'] = 'application/x-www-form-urlencoded;charset=UTF-8';
        init.body = params.toString();
      } else {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(body);
      }
      return fetchFn(url, init).then(function (res) {
        return res && res.json ? res.json() : null;
      }).then(function (data) {
        if (destroyed) return data;
        if (data && data.ok) applySnapshot(data, { replace: true });
        if (data && data.is_authoritative && channel) {
          try { channel.postMessage({ type: 'authoritative', session_id: sessionId }); } catch (e) {}
        }
        return data;
      }).catch(function () { return null; });
    }

    function startIfAllowed() {
      if (destroyed || completed || manuallyPaused) return;
      if (visibilityOf(doc) !== 'visible') return;
      started = true;
      awaitingServer = true;
      post('start').then(function (data) {
        awaitingServer = false;
        if (data && data.is_authoritative) foreignHold = false;
        render();
      });
    }

    function syncTicker() {
      if (raf && root.cancelAnimationFrame) root.cancelAnimationFrame(raf);
      raf = null;
      if (heartbeatTimer && root.clearInterval) root.clearInterval(heartbeatTimer);
      heartbeatTimer = null;
      if (destroyed || !shouldRunLocally({
        completed: completed,
        manually_paused: manuallyPaused,
        is_authoritative: authoritative,
        status: status,
      }, visibilityOf(doc))) {
        render();
        return;
      }
      function tick() {
        render();
        if (shouldRunLocally({
          completed: completed,
          manually_paused: manuallyPaused,
          is_authoritative: authoritative,
          status: status,
        }, visibilityOf(doc)) && root.requestAnimationFrame) {
          raf = root.requestAnimationFrame(tick);
        }
      }
      if (root.requestAnimationFrame) raf = root.requestAnimationFrame(tick);
      else render();
      if (options.enableHeartbeat !== false && root.setInterval) {
        heartbeatTimer = root.setInterval(function () { post('heartbeat'); }, heartbeatMs);
      }
    }

    function onHidden() {
      if (completed || manuallyPaused) return;
      var claimed = freezeOpenInterval();
      authoritative = false;
      status = 'auto_paused';
      syncTicker();
      return post('auto_pause', { claimed_ms: claimed }, true);
    }

    function onVisible() {
      if (completed || manuallyPaused) {
        render();
        return;
      }
      startIfAllowed();
    }

    function pauseManual() {
      if (completed) return;
      var claimed = freezeOpenInterval();
      manuallyPaused = true;
      authoritative = false;
      status = 'manually_paused';
      foreignHold = false;
      syncTicker();
      return post('pause', { claimed_ms: claimed }, true);
    }

    function resumeManual() {
      if (completed) return;
      manuallyPaused = false;
      foreignHold = false;
      started = true;
      awaitingServer = true;
      post('resume').then(function (data) {
        awaitingServer = false;
        if (data && data.is_authoritative) foreignHold = false;
        render();
      });
    }

    function markComplete(snap) {
      completed = true;
      manuallyPaused = false;
      authoritative = false;
      status = 'completed';
      if (snap) applySnapshot(snap, { replace: true });
      else {
        var claimed = freezeOpenInterval();
        render();
        post('complete', { claimed_ms: claimed }, true);
      }
      syncTicker();
    }

    if (options.listenDocument !== false && doc && doc.addEventListener) {
      doc.addEventListener('visibilitychange', function () {
        if (visibilityOf(doc) === 'hidden') onHidden();
        else onVisible();
      });
      root.addEventListener && root.addEventListener('pagehide', onHidden);
      doc.addEventListener('interoves:daily-timing', function (ev) {
        if (ev && ev.detail) {
          if (ev.detail.completed) markComplete(ev.detail);
          else applySnapshot(ev.detail, { replace: true });
        }
      });
    }

    if (options.enableBroadcast !== false && (channelFactory || (root.BroadcastChannel && url))) {
      try {
        channel = channelFactory ? channelFactory() : new root.BroadcastChannel('interoves-daily-timing:' + url);
        channel.onmessage = function (ev) {
          var data = ev && ev.data;
          if (!data || data.session_id === sessionId) return;
          if (data.type === 'authoritative' && !completed && !manuallyPaused) {
            foreignHold = true;
            authoritative = false;
            status = 'auto_paused';
            displayBaseMs = displayedMs();
            runningSince = null;
            render();
            syncTicker();
          }
        };
      } catch (e) {}
    }

    if (pauseBtn && pauseBtn.addEventListener) pauseBtn.addEventListener('click', pauseManual);
    if (resumeBtn && resumeBtn.addEventListener) resumeBtn.addEventListener('click', resumeManual);

    applySnapshot(bootstrap, { replace: true });
    if (solved && !exists) {
      completed = true;
      status = 'completed';
      if (pauseBtn) pauseBtn.hidden = true;
    }
    render();

    function boot() {
      if (destroyed) return;
      if (!fetchFn || !url) {
        if (!completed && !manuallyPaused) {
          authoritative = true;
          status = 'running';
          runningSince = nowMs(clock);
          started = true;
          syncTicker();
        }
        return;
      }
      var headers = { 'X-Requested-With': 'XMLHttpRequest' };
      var anon = getAnonKey();
      if (anon) headers['X-Interoves-Anon'] = anon;
      var getUrl = url;
      if (sessionId) {
        getUrl += (url.indexOf('?') >= 0 ? '&' : '?') + 'session_id=' + encodeURIComponent(sessionId);
      }
      fetchFn(getUrl, { credentials: 'same-origin', headers: headers }).then(function (res) {
        return res && res.json ? res.json() : null;
      }).then(function (data) {
        if (destroyed) return;
        if (data) applySnapshot(data, { replace: true });
        if (!completed && !manuallyPaused) {
          if (root.requestAnimationFrame) {
            root.requestAnimationFrame(function () {
              root.requestAnimationFrame(startIfAllowed);
            });
          } else startIfAllowed();
        }
      }).catch(function () {
        if (!destroyed && !completed && !manuallyPaused) startIfAllowed();
      });
    }

    return {
      formatElapsed: formatElapsed,
      displayedMs: displayedMs,
      state: currentState,
      applySnapshot: applySnapshot,
      startIfAllowed: startIfAllowed,
      pauseManual: pauseManual,
      resumeManual: resumeManual,
      markComplete: markComplete,
      onHidden: onHidden,
      onVisible: onVisible,
      boot: boot,
      destroy: function () {
        destroyed = true;
        awaitingServer = false;
        if (heartbeatTimer && root.clearInterval) root.clearInterval(heartbeatTimer);
        heartbeatTimer = null;
        if (raf && root.cancelAnimationFrame) root.cancelAnimationFrame(raf);
        raf = null;
        if (channel) {
          try {
            if (typeof channel.close === 'function') channel.close();
          } catch (e) {}
          channel = null;
        }
      },
    };
  }

  function bindFromDocument(doc, extras) {
    extras = extras || {};
    doc = doc || (root && root.document);
    if (!doc) return null;
    var bootstrapEl = doc.getElementById('daily-timing-bootstrap');
    if (!bootstrapEl) return null;
    var bootstrap = {};
    try { bootstrap = JSON.parse(bootstrapEl.textContent || '{}'); } catch (e) {}
    var timerRoot = doc.querySelector('[data-daily-timer]');
    var overlay = doc.querySelector('[data-daily-pause-overlay]');
    var board = doc.querySelector('[data-daily-solve-board]');
    var url = extras.url || (timerRoot && timerRoot.getAttribute('data-timing-url')) || '';
    var solved = extras.solved;
    if (solved == null) {
      var card = doc.querySelector('.new-taskcard[data-solved], [data-alphabetty-meta][data-solved]');
      solved = !!(card && card.getAttribute('data-solved') === '1');
    }
    var controller = create({
      document: doc,
      url: url,
      bootstrap: bootstrap,
      solved: solved,
      getAnonKey: extras.getAnonKey,
      getCsrf: extras.getCsrf,
      root: timerRoot,
      displayEl: doc.querySelector('[data-daily-timer-display]'),
      pauseBtn: doc.querySelector('[data-daily-timer-pause]'),
      overlay: overlay,
      resumeBtn: doc.querySelector('[data-daily-timer-resume]'),
      overlayTitle: doc.querySelector('[data-daily-pause-title]'),
      overlayText: doc.querySelector('[data-daily-pause-text]'),
      boardEl: board,
    });
    controller.boot();
    return controller;
  }

  return {
    formatElapsed: formatElapsed,
    shouldRunLocally: shouldRunLocally,
    create: create,
    bindFromDocument: bindFromDocument,
    HEARTBEAT_MS: HEARTBEAT_MS,
  };
});
