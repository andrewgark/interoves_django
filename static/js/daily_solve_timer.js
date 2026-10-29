'use strict';

/**
 * Active solving timer for one task group.
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
  // A visible tab nobody touches is not solving. Generous on purpose: people
  // think for minutes over a wall or an article without any input.
  var IDLE_MS = 10 * 60 * 1000;
  // How long a visible page may go without a running clock before it reports.
  var WATCHDOG_MS = 30000;
  // A stopped clock on a page the player is using gets a restart attempt.
  var HEAL_AFTER_MS = 10000;
  var HEAL_THROTTLE_MS = 10000;
  var STOPPED_REPORT_AFTER_MS = 60000;
  var ACTIVITY_EVENTS = ['pointerdown', 'pointermove', 'keydown', 'input', 'wheel', 'touchstart', 'focusin'];
  var EVENT_QUEUE_LIMIT = 8;
  var EVENT_MAX_ATTEMPTS = 3;
  var EVENT_MAX_AGE_MS = 20000;
  // A request on a silently dead connection can hang for half a minute and
  // hold the whole queue; give up early so a retry can use a fresh one.
  var REQUEST_TIMEOUT_MS = 10000;
  var RETRY_AFTER_MAX_MS = 10000;
  // A start the browser rejects at once (Safari on macOS, status 0) used up
  // all six attempts in under 8 s. Keep retrying for this long, within a cap.
  var START_RETRY_WINDOW_MS = 30000;
  var START_MIN_ATTEMPTS = 6;
  var START_MAX_ATTEMPTS = 12;
  var RETRY_AFTER_FLOOR_MAX_MS = 8000;
  var WINDOW_SESSION_PREFIX = 'interoves-daily-timing:';
  // A page that still gets reload_required right after reloading really
  // disagrees with the server; do not reload it again within this window.
  var RELOAD_GUARD_MS = 60000;

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

  function formatClock(ms) {
    var seconds = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    var secs = seconds % 60;
    if (hours) return String(hours).padStart(2, '0') + ':' + String(minutes).padStart(2, '0') + ':' + String(secs).padStart(2, '0');
    return String(minutes).padStart(2, '0') + ':' + String(secs).padStart(2, '0');
  }

  function parseRetryAfter(value, now) {
    if (value == null || String(value).trim() === '') return 0;
    var raw = String(value).trim();
    var seconds = Number(raw);
    var delay = isFinite(seconds)
      ? Math.max(0, seconds * 1000)
      : Math.max(0, Date.parse(raw) - (now || Date.now()));
    return isFinite(delay) ? Math.min(RETRY_AFTER_FLOOR_MAX_MS, delay) : 0;
  }

  function retryDelayMs(retryAfterMs, attempt, randomValue) {
    var floor = Math.max(0, Math.min(RETRY_AFTER_FLOOR_MAX_MS, Number(retryAfterMs) || 0));
    var spread = Math.min(4000, 250 * Math.pow(2, Math.max(0, attempt || 0)));
    spread = Math.min(spread, RETRY_AFTER_MAX_MS - floor);
    var random = Math.max(0, Math.min(0.999999, Number(randomValue) || 0));
    return Math.min(RETRY_AFTER_MAX_MS, floor + (spread ? Math.max(1, Math.round(spread * random)) : 0));
  }

  function shouldRunLocally(state, visibility) {
    if (!state || state.completed || state.manually_paused) return false;
    if (state.status === 'manually_paused' || state.status === 'completed') return false;
    if (visibility && visibility !== 'visible') return false;
    return !!state.is_authoritative && state.status === 'running';
  }

  // static/js/client_event_id.js is loaded before this file.
  function isUuid(value) {
    return root.InterovesClientEventId.isUuid(value);
  }

  function uuid() {
    return root.InterovesClientEventId.create();
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
    var gameplayContext = options.gameplayContext || '';
    var getAnonKey = options.getAnonKey || function () { return ''; };
    var getCsrf = options.getCsrf || function () { return ''; };
    var fetchFn = options.fetch || (root && root.fetch && root.fetch.bind(root));
    var storage = options.storage || (root && root.sessionStorage);
    var windowIdentity = options.windowIdentity || (
      root && root.window === root ? root : null
    );
    var clock = options.clock || root.performance || { now: function () { return Date.now(); } };
    var bootstrap = options.bootstrap || {};
    var solved = !!options.solved;
    var heartbeatMs = options.heartbeatMs || HEARTBEAT_MS;
    var reloadPage = options.reload || function () {
      if (root.location && root.location.reload) root.location.reload();
    };
    var idleMs = options.idleMs || IDLE_MS;

    var rootEl = options.root;
    var displayEl = options.displayEl;
    var pauseBtn = options.pauseBtn;
    var toggleBtn = options.toggleBtn;
    var toggleLabel = options.toggleLabel;
    var tooltipValue = options.tooltipValue;
    var popover = options.popover;
    var errorEl = options.errorEl;
    var overlay = options.overlay;
    var resumeBtn = options.resumeBtn;
    var overlayTitle = options.overlayTitle;
    var overlayText = options.overlayText;
    var boardEl = options.boardEl;

    var sessionKey = 'session:' + url;
    // Pre-fix clients kept seq per URL. The server checks seq per session, and
    // one task group is reachable through several URLs (number, share hash),
    // so a per-URL counter restarted at 1 and the server ignored the start.
    var legacySeqKey = sessionKey + ':seq';
    var seqKey = legacySeqKey;
    var sessionId = '';
    var seq = 0;
    try {
      var windowName = windowIdentity && typeof windowIdentity.name === 'string'
        ? windowIdentity.name
        : '';
      var namedSession = windowName.indexOf(WINDOW_SESSION_PREFIX) === 0
        ? windowName.slice(WINDOW_SESSION_PREFIX.length)
        : '';
      // sessionStorage can be cloned into a window.open() child.  A
      // window.name marker survives reloads but is not cloned, so an unnamed
      // child gets a genuinely new lease instead of reusing its opener's ID.
      // IDs minted by the old non-UUID fallback are never accepted as a lease
      // by the server; replace them instead of reusing them forever.
      if (isUuid(namedSession)) {
        sessionId = namedSession;
      } else if (windowIdentity && (!windowName || namedSession)) {
        sessionId = uuid();
        windowIdentity.name = WINDOW_SESSION_PREFIX + sessionId;
      } else {
        sessionId = (storage && storage.getItem(sessionKey)) || '';
        if (!isUuid(sessionId)) sessionId = '';
      }
      if (sessionId && storage && storage.getItem(sessionKey) !== sessionId) {
        storage.setItem(sessionKey, sessionId);
      }
    } catch (e) {}
    if (!sessionId) {
      sessionId = uuid();
      try { if (storage) storage.setItem(sessionKey, sessionId); } catch (e2) {}
    }
    seqKey = 'session-seq:' + sessionId;
    try {
      seq = Math.max(
        parseInt((storage && storage.getItem(seqKey)) || '0', 10) || 0,
        parseInt((storage && storage.getItem(legacySeqKey)) || '0', 10) || 0
      );
    } catch (e3) {}
    var committedMs = Number(bootstrap.committed_ms || bootstrap.accumulated_ms || 0) || 0;
    var displayBaseMs = Number(bootstrap.accumulated_ms || committedMs) || 0;
    var status = bootstrap.status || (solved ? 'completed' : 'auto_paused');
    var completed = !!bootstrap.completed || status === 'completed' || solved;
    var manuallyPaused = !!bootstrap.manually_paused || status === 'manually_paused';
    var resumeRequired = false;
    var authoritative = !!bootstrap.is_authoritative;
    var exists = !!bootstrap.exists;
    var runningSince = null;
    var heartbeatTimer = null;
    var startRetryTimer = null;
    var recoveryTimer = null;
    var lastActivityAt = Date.now();
    var idlePaused = false;
    var reportProblems = !!options.reportProblems;
    var createdAt = Date.now();
    var visibleSince = visibilityOf(doc) === 'visible' ? createdAt : null;
    var everRunning = false;
    var lastFailure = null;
    var reportsSent = {};
    var watchdogTimer = null;
    var stoppedSince = null;
    var lastHealAt = 0;
    var startAttempts = 0;
    // Unlike startAttempts, automatic recovery does not reset this: it counts
    // every failed start since the clock last started, for the report.
    var startFailures = 0;
    var startFailed = false;
    // Start rounds (a full retry budget each) that failed since the clock
    // last started. Recovery runs a second round; only that one is reported.
    var failedStartRounds = 0;
    var startRoundStartedAt = 0;
    var startNetworkFailed = false;
    var startEvent = null;
    var startEventCreatedAt = 0;
    var timingEventCapability = bootstrap.timing_event_capability === 'stable-event-v1';
    var eventQueue = [];
    var eventQueueRunning = false;
    var deliveryFailed = false;
    // The server said this page can never run a timer (e.g. the game was
    // already finished: replay_required). Only a reload helps; stop sending.
    var reloadRequired = false;
    var raf = null;
    var destroyed = false;
    var awaitingServer = false;
    var lastAppliedRequestSeq = 0;
    var stateRevision = 0;
    var lastPausedSignal = null;
    var startPending = false;
    // Wall-clock start of visible time the server has not confirmed because
    // the lease's heartbeats got lost. Sent as claimed_ms when the tab
    // restarts, so the server can credit it within its own bounds.
    var unconfirmedSince = null;
    // Offline play (docs/offline-play-plan.md): once a request failed on the
    // network, the next start/resume is marked offline so the server may
    // credit the whole stretch (still bounded by its own clock). The page
    // enables this only when the player may play offline.
    var offlineCredit = false;
    var lostNetwork = false;

    function advanceStateRevision() {
      stateRevision += 1;
      if (rootEl && rootEl.setAttribute) {
        rootEl.setAttribute('data-timing-state-revision', String(stateRevision));
      }
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
        session_id: sessionId,
        accumulated_ms: displayedMs(),
        committed_ms: committedMs,
        is_authoritative: authoritative,
        manually_paused: manuallyPaused,
        completed: completed,
        exists: exists,
        resume_required: resumeRequired,
      };
    }

    function render() {
      var ms = displayedMs();
      if (displayEl) displayEl.textContent = formatElapsed(ms);
      if (toggleBtn) {
        var paused = manuallyPaused || resumeRequired;
        toggleBtn.setAttribute('aria-expanded', (!paused && popover && !popover.hidden) ? 'true' : 'false');
        toggleBtn.setAttribute('aria-label', paused ? 'Продолжить задание' : 'Показать время решения');
      }
      if (tooltipValue) tooltipValue.textContent = formatClock(ms);
      if (errorEl) {
        errorEl.hidden = !(startFailed || deliveryFailed || reloadRequired);
        errorEl.textContent = reloadRequired
          ? 'Страница устарела. Обновите её, чтобы продолжить.'
          : startFailed
          ? 'Не удалось запустить таймер. Время решения пока не записывается.'
          : (deliveryFailed
            ? 'Не удалось подтвердить сохранение времени. Проверьте соединение и обновите состояние таймера.'
            : '');
      }
      if (toggleLabel) toggleLabel.textContent = (manuallyPaused || resumeRequired) ? 'Продолжить' : '';
      if (popover && (manuallyPaused || resumeRequired)) popover.hidden = true;
      if (rootEl) {
        rootEl.hidden = completed;
        rootEl.classList.toggle('is-paused', manuallyPaused || (!authoritative && !completed && status !== 'running'));
        rootEl.classList.toggle('is-completed', completed);
      }
      if (pauseBtn) pauseBtn.hidden = completed || resumeRequired;
      var showOverlay = !completed && (manuallyPaused || resumeRequired);
      if (rootEl && rootEl.setAttribute) {
        rootEl.setAttribute('data-timing-session-id', sessionId);
        rootEl.setAttribute('data-timing-state-revision', String(stateRevision));
      }
      var wrap = boardEl && boardEl.closest ? boardEl.closest('.new-daily-solve') : null;
      if (wrap) {
        if (showOverlay) wrap.setAttribute('data-daily-paused', '');
        else wrap.removeAttribute('data-daily-paused');
      }
      // The raddle sticky pin is mounted on <body>, outside the board's
      // stacking context. Keep it out of the pause state as well, otherwise
      // it can paint above the pause overlay and remain interactive.
      if (doc && doc.body && showOverlay !== lastPausedSignal) {
        // render() runs on every animation frame while the clock ticks; the
        // raddle listener measures layout, so only signal real changes.
        lastPausedSignal = showOverlay;
        doc.body.classList.toggle('is-daily-solve-paused', showOverlay);
        if (typeof doc.dispatchEvent === 'function' && typeof root.CustomEvent === 'function') {
          doc.dispatchEvent(new root.CustomEvent('interoves:daily-solve-paused', {
            detail: { paused: showOverlay },
          }));
        }
      }
      if (overlay) {
        overlay.hidden = !showOverlay;
        if (boardEl) {
          if (showOverlay) boardEl.setAttribute('inert', '');
          else boardEl.removeAttribute('inert');
        }
      }
      if (overlayTitle) {
        overlayTitle.textContent = manuallyPaused
          ? 'Пауза'
          : (resumeRequired ? 'Таймер остановлен' : 'Игра открыта в другой вкладке');
      }
      if (overlayText) {
        overlayText.textContent = manuallyPaused
          ? 'Время остановлено. Нажмите «Продолжить», чтобы вернуться к заданию.'
          : (resumeRequired
            ? 'Вкладка долго не подтверждала активность. Время остановлено; нажмите «Продолжить», чтобы снова начать его учитывать.'
            : 'Это задание открыто в другом окне. Нажмите «Продолжить», чтобы вести время здесь.');
      }
    }

    function applySnapshot(snap, opts) {
      opts = opts || {};
      if (destroyed || !snap) return;
      var requestSeq = Number(opts.requestSeq);
      if (isFinite(requestSeq)) {
        if (requestSeq < lastAppliedRequestSeq) return;
        lastAppliedRequestSeq = requestSeq;
      }
      var clientRevision = Number(opts.clientRevision);
      if (isFinite(clientRevision) && clientRevision < stateRevision) return;
      var incomingRunning = snap.status === 'running' && !snap.completed && !snap.manually_paused;
      if (incomingRunning && manuallyPaused) return;
      if (incomingRunning && visibilityOf(doc) === 'hidden') return;
      advanceStateRevision();
      exists = snap.exists !== false;
      completed = completed || !!snap.completed || snap.status === 'completed';
      if (completed) {
        status = 'completed';
        manuallyPaused = false;
        resumeRequired = false;
      } else {
        manuallyPaused = !!snap.manually_paused || snap.status === 'manually_paused';
        status = snap.status || status;
        if (snap.resume_required) resumeRequired = true;
        if (incomingRunning && snap.is_authoritative && !snap.resume_required) resumeRequired = false;
      }
      if (typeof snap.committed_ms === 'number') committedMs = snap.committed_ms;
      var incoming = Number(snap.accumulated_ms);
      if (completed && snap.frozen_ms != null) {
        displayBaseMs = Math.max(0, Number(snap.frozen_ms) || 0);
      } else if (!isNaN(incoming)) {
        // Never jump down: pause/hidden responses can lag behind the local monotonic tick.
        displayBaseMs = Math.max(displayBaseMs, incoming);
      }
      authoritative = !!snap.is_authoritative && !snap.resume_required;
      if (authoritative && status === 'running' && !completed && !manuallyPaused) {
        runningSince = nowMs(clock);
        everRunning = true;
        unconfirmedSince = null;
        lostNetwork = false;
      }
      if (completed || manuallyPaused) unconfirmedSince = null;
      render();
      syncTicker();
    }

    // The clock was running when the server stopped confirming it.
    function noteUnconfirmedFromRunning() {
      if (runningSince == null || unconfirmedSince != null) return;
      if (completed || manuallyPaused || visibilityOf(doc) !== 'visible') return;
      unconfirmedSince = Date.now() - Math.max(0, nowMs(clock) - runningSince);
    }

    // Visible time since the last confirmation, up to the idle cut-off.
    function unconfirmedMs() {
      if (unconfirmedSince == null || completed || manuallyPaused) return 0;
      var end = Math.min(Date.now(), lastActivityAt + idleMs);
      return Math.max(0, Math.floor(end - unconfirmedSince));
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

    function makeEvent(action, extra) {
      extra = extra || {};
      var eventSeq = nextSeq();
      var claimed = Object.prototype.hasOwnProperty.call(extra, 'claimed_ms')
        ? Math.max(0, Math.floor(Number(extra.claimed_ms) || 0))
        : currentClaimedMs();
      var event = {
        action: action,
        session_id: sessionId,
        event_id: extra.event_id || uuid(),
        seq: eventSeq,
        claimed_ms: claimed,
        // The server clock remains authoritative for duration.  This is only
        // a bounded ordering hint for event-log replay when requests arrive
        // out of order.
        client_occurred_at: new Date().toISOString(),
        anon_key: getAnonKey() || '',
      };
      if (offlineCredit && lostNetwork && (action === 'start' || action === 'resume')) event.offline = true;
      return event;
    }

    function requestEventOnce(body, keepalive) {
      if (options.offline) return Promise.resolve({ data: null, status: 0, retryable: false });
      var headers = {
        'X-CSRFToken': getCsrf(),
        'X-Requested-With': 'XMLHttpRequest',
      };
      var anon = getAnonKey();
      if (anon) headers['X-Interoves-Anon'] = anon;
      if (gameplayContext) headers['X-Interoves-Gameplay-Context'] = gameplayContext;
      if (!fetchFn) return Promise.resolve({ data: null, status: 0, retryable: false });
      var init = {
        method: 'POST',
        credentials: 'same-origin',
        keepalive: !!keepalive,
        headers: headers,
      };
      try {
        if (root.AbortSignal && typeof root.AbortSignal.timeout === 'function') {
          init.signal = root.AbortSignal.timeout(REQUEST_TIMEOUT_MS);
        }
      } catch (e) {}
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
        if (!res || !res.json) return { data: null, status: 0, retryable: false };
        var responseStatus = Number(res.status) || 0;
        var responseRetryAfterMs = res.headers && res.headers.get
          ? parseRetryAfter(res.headers.get('Retry-After'), Date.now())
          : 0;
        return res.json().then(function (data) {
          if (data && responseStatus) {
            data._http_status = responseStatus;
            data._retry_after_ms = responseRetryAfterMs;
          }
          return {
            data: data,
            status: responseStatus,
            retryAfterMs: responseRetryAfterMs,
            retryable: responseStatus === 503 || (
              !timingEventCapability
              && responseStatus === 409
              && data
              && data.error === 'busy'
            ),
          };
        }, function () {
          return {
            data: null,
            status: responseStatus,
            retryAfterMs: responseRetryAfterMs,
            retryable: responseStatus === 503,
          };
        });
      }).catch(function (err) {
        // TimeoutError: our own abort, the server did not answer in time.
        // TypeError: the request failed in the browser or on the way.
        lostNetwork = true;
        return {
          data: null,
          status: 0,
          retryable: true,
          networkError: true,
          errorName: (err && err.name) || '',
        };
      });
    }

    // A broken timer must never lock the board: the player keeps solving and
    // the timer quietly tries to start a fresh interval again later.
    function scheduleRecovery() {
      if (destroyed || reloadRequired || recoveryTimer || !root.setTimeout) return;
      recoveryTimer = root.setTimeout(function () {
        recoveryTimer = null;
        if (destroyed || completed || manuallyPaused || resumeRequired) return;
        if (authoritative && status === 'running') return;
        retryAfterConnectivity();
      }, heartbeatMs);
    }

    // E.g. a tab restored from the HTTP cache with HTML from before the game
    // was finished: a fresh page shows the finished game and its replay
    // button. Reload once; if that does not help, the message stays.
    function onReloadRequired() {
      if (reloadRequired) return;
      reloadRequired = true;
      if (destroyed || visibilityOf(doc) !== 'visible') return;
      var guardKey = 'timing-reload:' + url;
      var now = Date.now();
      try {
        var last = parseInt((storage && storage.getItem(guardKey)) || '0', 10) || 0;
        if (!storage || (last && now - last < RELOAD_GUARD_MS)) return;
        storage.setItem(guardKey, String(now));
      } catch (e) {
        return;
      }
      reloadPage();
    }

    function indicateDeliveryFailure() {
      deliveryFailed = true;
      if (!completed && status === 'running') {
        noteUnconfirmedFromRunning();
        authoritative = false;
        status = 'auto_paused';
        runningSince = null;
        advanceStateRevision();
        syncTicker();
      }
      render();
      scheduleRecovery();
    }

    function failQueuedEvents(data) {
      var failed = eventQueue.splice(0);
      indicateDeliveryFailure();
      failed.forEach(function (item) { item.resolve(data || null); });
    }

    function pumpEventQueue() {
      if (eventQueueRunning || !eventQueue.length || destroyed) return;
      eventQueueRunning = true;
      (async function () {
        while (eventQueue.length && !destroyed) {
          var item = eventQueue[0];
          var result = null;
          while (item.attempts < item.maxAttempts && Date.now() - item.createdAt < EVENT_MAX_AGE_MS) {
            item.attempts += 1;
            result = await requestEventOnce(item.body, item.keepalive);
            if (result.data && result.data.reload_required) onReloadRequired();
            if (!(result.data && result.data.ok)) {
              lastFailure = {
                action: item.body.action,
                status: result.status || 0,
                error: (result.data && result.data.error) || '',
                network: !!result.networkError,
                errorName: result.errorName || '',
                at: Date.now(),
              };
            }
            // navigator.onLine can be stuck at false while the network works
            // (seen in Chrome with VPN/virtual adapters). Gating sends on it
            // kept the start event from ever leaving, while gameplay requests
            // went through and were rejected for lacking a lease. Only wait
            // for the "online" event after a send has actually failed.
            if (
              result.networkError
              && root.navigator && root.navigator.onLine === false
              && item.attempts < item.maxAttempts
            ) {
              item.stalled = true;
              eventQueueRunning = false;
              if (root.setTimeout && !item.expiryTimer) {
                item.expiryTimer = root.setTimeout(function () {
                  if (eventQueue[0] === item) failQueuedEvents({ ok: false, error: 'delivery_timeout' });
                }, Math.max(0, EVENT_MAX_AGE_MS - (Date.now() - item.createdAt)));
              }
              return;
            }
            var data = result.data;
            if (data && data.ok && (!result.status || (result.status >= 200 && result.status < 300))) {
              if (result.status) data._http_status = result.status;
              deliveryFailed = false;
              if (!destroyed) applySnapshot(data, { replace: true, requestSeq: item.body.seq });
              if (
                !destroyed
                && item.body.action === 'heartbeat'
                && data.status !== 'completed'
                && !data.completed
                && !data.manually_paused
                && data.status !== 'manually_paused'
                && !data.is_authoritative
              ) {
                // The server expired this tab's lease although the tab is
                // visible, i.e. heartbeats did not get through. That is a
                // timer failure, not the player's: keep the board usable and
                // start a fresh interval once the queue drains.
                noteUnconfirmedFromRunning();
                authoritative = false;
                status = 'auto_paused';
                runningSince = null;
                var serverElapsed = Number(data.accumulated_ms);
                if (isFinite(serverElapsed)) displayBaseMs = Math.max(0, serverElapsed);
                var serverCommitted = Number(data.committed_ms);
                if (isFinite(serverCommitted)) committedMs = Math.max(0, serverCommitted);
                advanceStateRevision();
                syncTicker();
                if (root.setTimeout) root.setTimeout(retryAfterConnectivity, 0);
                scheduleRecovery();
              }
              eventQueue.shift();
              if (item.expiryTimer && root.clearTimeout) root.clearTimeout(item.expiryTimer);
              item.resolve(data);
              result = null;
              break;
            }
            if (!result.retryable || item.attempts >= item.maxAttempts) break;
            var random = (root.Math || Math).random();
            await new Promise(function (resolve) {
              root.setTimeout(resolve, retryDelayMs(result.retryAfterMs, item.attempts - 1, random));
            });
          }
          // An item can reach the head already too old to send, e.g. queued
          // behind a slow pause, or resumed by "online" after its deadline.
          // Without a result the outer loop would pick it again forever
          // without awaiting and freeze the page.
          if (!result && eventQueue[0] === item) result = { data: { ok: false, error: 'delivery_timeout' } };
          if (result) {
            // No stall here: only a send that is still worth retrying waits
            // for "online" (above), and that one carries an expiry timer.
            // A rejected or expired event stalled here blocked the queue for
            // good while navigator.onLine was stuck at false.
            failQueuedEvents(result.data);
            return;
          }
        }
      })().catch(function () {
        failQueuedEvents(null);
      }).finally(function () {
        eventQueueRunning = false;
        if (eventQueue.length && !(eventQueue[0] && eventQueue[0].stalled)) pumpEventQueue();
        else if (!eventQueue.length && startPending) {
          startPending = false;
          startIfAllowed();
        }
      });
    }

    function sendEvent(body, keepalive, attempt, allowRetry) {
      if (options.offline) return Promise.resolve(null);
      if (timingEventCapability) {
        body.event_protocol = 1;
        body.event_capability = 'stable-event-v1';
      } else {
        delete body.event_protocol;
        delete body.event_capability;
      }
      return new Promise(function (resolve) {
        if (body.action === 'heartbeat') {
          var tailIndex = eventQueue.length - 1;
          var tail = eventQueue[tailIndex];
          if (tail && tail.body.action === 'heartbeat' && !(eventQueueRunning && tailIndex === 0)) {
            tail.resolve({ ok: false, error: 'heartbeat_coalesced' });
            eventQueue.pop();
          }
        }
        if (eventQueue.length >= EVENT_QUEUE_LIMIT) {
          indicateDeliveryFailure();
          resolve({ ok: false, error: 'timing_queue_full' });
          return;
        }
        eventQueue.push({
          body: body,
          keepalive: !!keepalive,
          attempts: 0,
          maxAttempts: allowRetry === false ? 1 : EVENT_MAX_ATTEMPTS,
          createdAt: Date.now(),
          resolve: resolve,
          stalled: false,
          expiryTimer: null,
        });
        pumpEventQueue();
      });
    }

    function retryAfterConnectivity() {
      // A start in flight settles and retries on its own. Resetting here
      // dropped its event, so the next send could not be deduplicated, and
      // queued an immediate extra start that skipped the backoff.
      if (awaitingServer) return;
      if (startFailed || deliveryFailed) {
        startFailed = false;
        startAttempts = 0;
        startEvent = null;
        startEventCreatedAt = 0;
      }
      startIfAllowed();
    }

    if (root.addEventListener) {
      root.addEventListener('online', function () {
        if (eventQueue[0]) eventQueue[0].stalled = false;
        pumpEventQueue();
        if (!completed && !manuallyPaused && !resumeRequired) retryAfterConnectivity();
      });
    }

    function post(action, extra, keepalive) {
      return sendEvent(makeEvent(action, extra), keepalive);
    }

    function startIfAllowed() {
      if (eventQueue.length || eventQueueRunning) {
        // E.g. a quick tab switch: the auto_pause is still in flight when the
        // page becomes visible again. Start once the queue drains instead of
        // leaving the clock stopped until the next visibility change.
        startPending = true;
        return;
      }
      if (
        destroyed || completed || manuallyPaused || resumeRequired || awaitingServer
        || startFailed || idlePaused || reloadRequired
      ) return;
      if (visibilityOf(doc) !== 'visible') return;
      if (startRetryTimer && root.clearTimeout) root.clearTimeout(startRetryTimer);
      startRetryTimer = null;
      awaitingServer = true;
      if (!startAttempts) startRoundStartedAt = Date.now();
      startAttempts += 1;
      // Retries reuse one event so a start that did reach the server is
      // deduplicated. Once it is too old to deliver, send a fresh one: on a
      // hanging connection two 10 s timeouts used up the age limit, and the
      // start failed after two of its six attempts.
      if (!startEvent || Date.now() - startEventCreatedAt >= EVENT_MAX_AGE_MS) {
        startEvent = makeEvent('start', { claimed_ms: unconfirmedMs() });
        startEventCreatedAt = Date.now();
      }
      // After a network error every other retry goes out as a keepalive
      // request: when Safari kept rejecting the start, the keepalive report
      // to the same URL still got through.
      var viaKeepalive = startNetworkFailed && startAttempts % 2 === 0;
      // The outer start policy owns its attempt budget. Retrying in both
      // layers would multiply attempts and could create a retry burst.
      sendEvent(startEvent, viaKeepalive, 0, false).then(function (data) {
        awaitingServer = false;
        if (data && data.ok) {
          startAttempts = 0;
          startFailures = 0;
          failedStartRounds = 0;
          startNetworkFailed = false;
          startFailed = false;
          startEvent = null;
          startEventCreatedAt = 0;
          releaseLeaseIfHidden(data);
          if (
            visibilityOf(doc) === 'visible'
            && data.status !== 'running' && data.status !== 'manually_paused'
            && data.status !== 'completed' && !data.completed && !data.manually_paused
          ) {
            // Accepted but not running, e.g. the server ignored the start as
            // stale. Nothing retries this on its own, so make it visible.
            reportProblem('start_not_running', { response_status: data.status || '' });
          }
          render();
          return;
        }
        startFailures += 1;
        startNetworkFailed = !!(lastFailure && lastFailure.action === 'start' && lastFailure.network);
        // Do not let an interval heartbeat overtake a start retry after an
        // unacknowledged start. The server snapshot on acknowledgement will
        // restore the authoritative running state.
        authoritative = false;
        if (!completed && !manuallyPaused) status = 'auto_paused';
        advanceStateRevision();
        syncTicker();
        // The tab got hidden while the start was in flight: nothing to run
        // now, and onVisible() retries. Marking it failed here showed the
        // error and alerted admins about a plain tab switch.
        if (!destroyed && visibilityOf(doc) !== 'visible') {
          render();
          return;
        }
        // A transient network/5xx/CSRF failure used to leave the clock frozen
        // at 0s forever. Retry a few times while the page is visible; a
        // gameplay-context error is deliberate and must be handled by reload.
        if (
          !destroyed
          && visibilityOf(doc) === 'visible'
          && !(data && data.reload_required)
          && startAttempts < START_MAX_ATTEMPTS
          && (
            startAttempts < START_MIN_ATTEMPTS
            || Date.now() - startRoundStartedAt < START_RETRY_WINDOW_MS
          )
          && root.setTimeout
        ) {
          var delay = retryDelayMs(
            Number(data && data._retry_after_ms) || 0,
            startAttempts - 1,
            (root.Math || Math).random()
          );
          if (startRetryTimer && root.clearTimeout) root.clearTimeout(startRetryTimer);
          startRetryTimer = root.setTimeout(function () {
            startRetryTimer = null;
            startIfAllowed();
          }, delay);
        } else if (!destroyed && !(data && data.reload_required)) {
          startFailed = true;
          failedStartRounds += 1;
          if (popover) popover.hidden = false;
          scheduleRecovery();
          // A short outage heals on the recovery round; alert only when that
          // one fails too.
          if (failedStartRounds >= 2) reportProblem('start_failed');
        } else if (reloadRequired && popover) {
          popover.hidden = false;
        }
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
      var lastShownSecond = -1;
      function tick() {
        var shownSecond = Math.floor(displayedMs() / 1000);
        if (shownSecond !== lastShownSecond) {
          lastShownSecond = shownSecond;
          render();
        }
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
        heartbeatTimer = root.setInterval(onHeartbeatTick, heartbeatMs);
      }
    }

    // onHidden() has nothing to pause while a start/resume is in flight. If
    // the tab got hidden meanwhile, the server has just opened a lease that no
    // heartbeat will keep: close it now instead of letting it run until it
    // goes stale (~45 s of hidden time credited).
    function releaseLeaseIfHidden(data) {
      if (visibilityOf(doc) !== 'hidden' || destroyed || completed || manuallyPaused) return;
      if (!data || data.status !== 'running') return;
      post('auto_pause', { claimed_ms: 0 }, true);
    }

    function onHeartbeatTick() {
      if (Date.now() - lastActivityAt >= idleMs) {
        pauseForIdle();
        return;
      }
      post('heartbeat');
    }

    // Idle is an automatic stop like a hidden tab: no overlay, nothing to
    // press. The next input simply starts a fresh interval.
    function pauseForIdle() {
      unconfirmedSince = null;
      if (completed || manuallyPaused || !authoritative) return;
      idlePaused = true;
      var claimed = freezeOpenInterval();
      authoritative = false;
      status = 'auto_paused';
      advanceStateRevision();
      syncTicker();
      post('auto_pause', { claimed_ms: claimed }, false);
    }

    function noteActivity() {
      lastActivityAt = Date.now();
      if (idlePaused) {
        idlePaused = false;
        if (!completed && !manuallyPaused && !resumeRequired) retryAfterConnectivity();
        return;
      }
      healStoppedClock();
    }

    // Whatever stopped the clock (an auto_pause lost on unload, a bfcache
    // restore, a cause nobody anticipated), a player who is visibly working
    // should not stay on a dead timer. Retry, and report it once so we learn
    // about the cause.
    function healStoppedClock() {
      if (destroyed || completed || manuallyPaused || resumeRequired || reloadRequired) return;
      if (authoritative && status === 'running') {
        stoppedSince = null;
        return;
      }
      if (visibilityOf(doc) !== 'visible') return;
      var now = Date.now();
      if (stoppedSince == null) {
        stoppedSince = now;
        return;
      }
      if (now - stoppedSince < HEAL_AFTER_MS || now - lastHealAt < HEAL_THROTTLE_MS) return;
      if (startFailed || awaitingServer || eventQueue.length || eventQueueRunning) return;
      lastHealAt = now;
      if (everRunning && now - stoppedSince >= STOPPED_REPORT_AFTER_MS) {
        reportProblem('stopped_while_active');
      }
      retryAfterConnectivity();
    }

    function onHidden() {
      visibleSince = null;
      // Time hidden is not solving; a claim covers one visible stretch only.
      unconfirmedSince = null;
      if (completed || manuallyPaused || (!authoritative && status === 'auto_paused')) return;
      if (startRetryTimer && root.clearTimeout) root.clearTimeout(startRetryTimer);
      startRetryTimer = null;
      // Navigating into a private replay invalidates the official page's
      // gameplay context immediately. Do not send a stale auto_pause request
      // while the browser is unloading this page.
      if (doc && doc.documentElement && doc.documentElement.hasAttribute('data-replay-navigation')) return;
      var claimed = freezeOpenInterval();
      authoritative = false;
      status = 'auto_paused';
      advanceStateRevision();
      syncTicker();
      return post('auto_pause', { claimed_ms: claimed }, true);
    }

    // Tell the server a timer could not run so admins learn about it; the
    // player is never blocked by this. One report per reason per page.
    function reportProblem(reason, extra) {
      if (!reportProblems || destroyed || options.offline || !fetchFn || !url) return;
      if (reportsSent[reason]) return;
      reportsSent[reason] = true;
      var details = {
        online: root.navigator ? root.navigator.onLine : null,
        visibility: visibilityOf(doc),
        status: status,
        authoritative: authoritative,
        start_attempts: startAttempts,
        start_failed: startFailed,
        delivery_failed: deliveryFailed,
        awaiting_server: awaitingServer,
        resume_required: resumeRequired,
        manually_paused: manuallyPaused,
        idle_paused: idlePaused,
        queue_length: eventQueue.length,
        queue_stalled: !!(eventQueue[0] && eventQueue[0].stalled),
        last_action: lastFailure ? lastFailure.action : null,
        last_status: lastFailure ? lastFailure.status : null,
        last_error: lastFailure ? lastFailure.error : null,
        last_network: lastFailure ? lastFailure.network : null,
        last_error_name: lastFailure ? lastFailure.errorName : null,
        last_failure_age_s: lastFailure ? Math.round((Date.now() - lastFailure.at) / 1000) : null,
        start_failures: startFailures,
        failed_start_rounds: failedStartRounds,
        stopped_for_s: stoppedSince == null ? null : Math.round((Date.now() - stoppedSince) / 1000),
        session_id: sessionId,
        capability: timingEventCapability,
        page_age_s: Math.round((Date.now() - createdAt) / 1000),
      };
      Object.keys(extra || {}).forEach(function (key) { details[key] = extra[key]; });
      var headers = {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrf(),
        'X-Requested-With': 'XMLHttpRequest',
      };
      var anon = getAnonKey();
      if (anon) headers['X-Interoves-Anon'] = anon;
      try {
        fetchFn(url, {
          method: 'POST',
          credentials: 'same-origin',
          keepalive: true,
          headers: headers,
          body: JSON.stringify({ action: 'client_report', reason: reason, details: details }),
        }).catch(function () {});
      } catch (e) {}
    }

    // Catch-all for causes nobody anticipated (like a start that never left
    // the browser): a page visible for a while whose clock never ran.
    function scheduleWatchdog(delay) {
      if (!reportProblems || destroyed || watchdogTimer || !root.setTimeout) return;
      watchdogTimer = root.setTimeout(function () {
        watchdogTimer = null;
        if (destroyed || completed || everRunning || reloadRequired || Object.keys(reportsSent).length) return;
        // Failing starts report for themselves once recovery fails as well.
        if (manuallyPaused || resumeRequired || idlePaused || visibleSince == null || startFailures) {
          scheduleWatchdog();
          return;
        }
        var visibleFor = Date.now() - visibleSince;
        if (visibleFor < WATCHDOG_MS) {
          scheduleWatchdog(WATCHDOG_MS - visibleFor);
          return;
        }
        reportProblem('never_started');
      }, delay || WATCHDOG_MS);
    }

    function onVisible() {
      visibleSince = Date.now();
      stoppedSince = null;
      lastActivityAt = Date.now();
      idlePaused = false;
      if (completed || manuallyPaused || resumeRequired) {
        render();
        return;
      }
      retryAfterConnectivity();
    }

    function pauseManual() {
      if (completed) return;
      unconfirmedSince = null;
      var claimed = freezeOpenInterval();
      manuallyPaused = true;
      authoritative = false;
      status = 'manually_paused';
      advanceStateRevision();
      syncTicker();
      return post('pause', { claimed_ms: claimed }, false);
    }

    function resumeManual() {
      if (completed) return;
      var wasManuallyPaused = manuallyPaused;
      manuallyPaused = false;
      resumeRequired = false;
      awaitingServer = true;
      advanceStateRevision();
      return post('resume').then(function (data) {
        awaitingServer = false;
        if (data && data.ok) releaseLeaseIfHidden(data);
        if (!data || !data.ok) {
          // A deliberate pause stays until a resume succeeds: the server keeps
          // rejecting gameplay for it, and the player can simply press again.
          // Any other stop is a timer failure: unlock the board anyway and
          // keep retrying in the background.
          manuallyPaused = wasManuallyPaused || !!(data && (
            data.manually_paused || data.status === 'manually_paused'
          ));
          resumeRequired = false;
          authoritative = false;
          status = manuallyPaused ? 'manually_paused' : 'auto_paused';
          advanceStateRevision();
          if (!manuallyPaused) indicateDeliveryFailure();
          render();
        }
        render();
      });
    }

    function markComplete(snap) {
      completed = true;
      manuallyPaused = false;
      authoritative = false;
      status = 'completed';
      advanceStateRevision();
      if (snap) applySnapshot(snap, { replace: true });
      else {
        var claimed = freezeOpenInterval();
        render();
        post('complete', { claimed_ms: claimed }, false);
      }
      syncTicker();
    }

    if (options.listenDocument !== false && doc && doc.addEventListener) {
      ACTIVITY_EVENTS.forEach(function (name) {
        doc.addEventListener(name, noteActivity, { capture: true, passive: true });
      });
      if (root.addEventListener) root.addEventListener('scroll', noteActivity, { passive: true });
      doc.addEventListener('visibilitychange', function () {
        if (visibilityOf(doc) === 'hidden') onHidden();
        else onVisible();
      });
      root.addEventListener && root.addEventListener('pagehide', onHidden);
      // A page restored from the back/forward cache resumes without a fresh
      // boot; treat it like the tab becoming visible again.
      root.addEventListener && root.addEventListener('pageshow', function (event) {
        if (event && event.persisted && visibilityOf(doc) === 'visible') onVisible();
      });
      doc.addEventListener('submit', function (event) {
        var form = event && event.target;
        if (form && form.closest && form.closest('form[data-replay-submit]') && doc.documentElement) {
          doc.documentElement.setAttribute('data-replay-navigation', '1');
        }
      }, true);
      doc.addEventListener('interoves:daily-timing', function (ev) {
        if (ev && ev.detail) {
          var clientRevision = Number(ev.detail._client_revision);
          // Completion is terminal. A visibility/pause event can advance the
          // local revision while a successful gameplay request is in flight;
          // never let that stale request revision hide the solved state.
          if (ev.detail.completed) markComplete(ev.detail);
          else if (!isFinite(clientRevision) || clientRevision >= stateRevision) {
            applySnapshot(ev.detail, {
              replace: true,
              clientRevision: clientRevision,
            });
          }
        }
      });
    }

    if (pauseBtn && pauseBtn.addEventListener) pauseBtn.addEventListener('click', pauseManual);
    if (resumeBtn && resumeBtn.addEventListener) resumeBtn.addEventListener('click', resumeManual);
    if (toggleBtn && toggleBtn.addEventListener) toggleBtn.addEventListener('click', function () {
      if (manuallyPaused || resumeRequired) {
        resumeManual();
        return;
      }
      if (popover) {
        popover.hidden = !popover.hidden;
        toggleBtn.setAttribute('aria-expanded', popover.hidden ? 'false' : 'true');
      }
    });
    if (doc && doc.addEventListener) doc.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && popover && !popover.hidden) {
        popover.hidden = true;
        if (toggleBtn) toggleBtn.setAttribute('aria-expanded', 'false');
      }
    });

    applySnapshot(bootstrap, { replace: true });
    if (solved && !exists) {
      completed = true;
      status = 'completed';
      if (pauseBtn) pauseBtn.hidden = true;
    }
    render();

    function boot() {
      if (destroyed) return;
      scheduleWatchdog();
      if (!fetchFn || !url) {
        if (!completed && !manuallyPaused) {
          authoritative = true;
          status = 'running';
          runningSince = nowMs(clock);
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
        // An error body (403 club_required, 503 busy, ...) is not a timing
        // snapshot: applying it would e.g. flip `exists` to true.
        if (!res || !res.json || res.ok === false) return null;
        return res.json();
      }).then(function (data) {
        if (destroyed) return;
        if (data && data.ok === false) data = null;
        timingEventCapability = !!(data && data.timing_event_capability === 'stable-event-v1');
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
      formatClock: formatClock,
      state: currentState,
      applySnapshot: applySnapshot,
      startIfAllowed: startIfAllowed,
      pauseManual: pauseManual,
      resumeManual: resumeManual,
      markComplete: markComplete,
      onHidden: onHidden,
      onVisible: onVisible,
      noteActivity: noteActivity,
      unconfirmedMs: unconfirmedMs,
      allowOfflineCredit: function (allowed) { offlineCredit = !!allowed; },
      // A request failed on the network and the server has not confirmed the
      // clock since: a claim for that stretch may still be on its way.
      offlineClaimPending: function () { return lostNetwork && !completed && !manuallyPaused; },
      boot: boot,
      destroy: function () {
        destroyed = true;
        awaitingServer = false;
        if (watchdogTimer && root.clearTimeout) root.clearTimeout(watchdogTimer);
        watchdogTimer = null;
        if (startRetryTimer && root.clearTimeout) root.clearTimeout(startRetryTimer);
        startRetryTimer = null;
        if (recoveryTimer && root.clearTimeout) root.clearTimeout(recoveryTimer);
        recoveryTimer = null;
        if (heartbeatTimer && root.clearInterval) root.clearInterval(heartbeatTimer);
        heartbeatTimer = null;
        if (raf && root.cancelAnimationFrame) root.cancelAnimationFrame(raf);
        raf = null;
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
      // Walls, replacements and palindromes have many tasks per group: the
      // first solved card does not mean the group is done. (A completed group
      // does not render the timer at all.)
      var cards = doc.querySelectorAll('.new-taskcard[data-solved], [data-alphabetty-meta][data-solved]');
      solved = cards.length > 0 && Array.prototype.every.call(cards, function (card) {
        return card.getAttribute('data-solved') === '1';
      });
    }
    var controller = create({
      document: doc,
      url: url,
      bootstrap: bootstrap,
      solved: solved,
      getAnonKey: extras.getAnonKey,
      getCsrf: extras.getCsrf,
      gameplayContext: extras.gameplayContext || (timerRoot && timerRoot.getAttribute('data-gameplay-context')) || '',
      root: timerRoot,
      displayEl: doc.querySelector('[data-daily-timer-display]'),
      pauseBtn: doc.querySelector('[data-daily-timer-pause]'),
      toggleBtn: doc.querySelector('[data-daily-timer-toggle]'),
      toggleLabel: doc.querySelector('[data-daily-timer-toggle-label]'),
      tooltipValue: doc.querySelector('[data-daily-timer-tooltip-value]'),
      errorEl: doc.querySelector('[data-daily-timer-error]'),
      popover: doc.querySelector('[data-daily-timer-popover]'),
      overlay: overlay,
      resumeBtn: doc.querySelector('[data-daily-timer-resume]'),
      overlayTitle: doc.querySelector('[data-daily-pause-title]'),
      overlayText: doc.querySelector('[data-daily-pause-text]'),
      boardEl: board,
      reportProblems: true,
    });
    controller.boot();
    api.current = controller;
    return controller;
  }

  var api = {
    formatElapsed: formatElapsed,
    formatClock: formatClock,
    parseRetryAfter: parseRetryAfter,
    retryDelayMs: retryDelayMs,
    shouldRunLocally: shouldRunLocally,
    create: create,
    bindFromDocument: bindFromDocument,
    HEARTBEAT_MS: HEARTBEAT_MS,
    IDLE_MS: IDLE_MS,
    current: null,
  };
  return api;
});
