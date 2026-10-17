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
  var EVENT_QUEUE_LIMIT = 8;
  var EVENT_MAX_ATTEMPTS = 3;
  var EVENT_MAX_AGE_MS = 20000;
  var RETRY_AFTER_MAX_MS = 10000;
  var RETRY_AFTER_FLOOR_MAX_MS = 8000;
  var STORAGE_PREFIX = 'interoves_task_group_timing_v1:';
  var WINDOW_SESSION_PREFIX = 'interoves-daily-timing:';

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
    var gameplayContext = options.gameplayContext || '';
    var getAnonKey = options.getAnonKey || function () { return ''; };
    var getCsrf = options.getCsrf || function () { return ''; };
    var fetchFn = options.fetch || (root && root.fetch && root.fetch.bind(root));
    var storage = options.storage || (root && root.sessionStorage);
    var localStore = options.localStorage || (root && root.localStorage);
    var windowIdentity = options.windowIdentity || (
      root && root.window === root ? root : null
    );
    var clock = options.clock || root.performance || { now: function () { return Date.now(); } };
    var bootstrap = options.bootstrap || {};
    var solved = !!options.solved;
    var heartbeatMs = options.heartbeatMs || HEARTBEAT_MS;
    var onState = options.onState || function () {};

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
    var seqKey = sessionKey + ':seq';
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
      if (namedSession) {
        sessionId = namedSession;
      } else if (windowIdentity && !windowName) {
        sessionId = uuid();
        windowIdentity.name = WINDOW_SESSION_PREFIX + sessionId;
      } else {
        sessionId = (storage && storage.getItem(sessionKey)) || '';
      }
      if (sessionId && storage && storage.getItem(sessionKey) !== sessionId) {
        storage.setItem(sessionKey, sessionId);
      }
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
    var resumeRequired = false;
    var authoritative = !!bootstrap.is_authoritative;
    var exists = !!bootstrap.exists;
    var runningSince = null;
    var heartbeatTimer = null;
    var startRetryTimer = null;
    var startAttempts = 0;
    var startFailed = false;
    var startEvent = null;
    var startEventCreatedAt = 0;
    var timingEventCapability = bootstrap.timing_event_capability === 'stable-event-v1';
    var eventQueue = [];
    var eventQueueRunning = false;
    var deliveryFailed = false;
    var raf = null;
    var started = false;
    var destroyed = false;
    var awaitingServer = false;
    var lastAppliedRequestSeq = 0;
    var stateRevision = 0;

    function advanceStateRevision() {
      stateRevision += 1;
      if (rootEl && rootEl.setAttribute) {
        rootEl.setAttribute('data-timing-state-revision', String(stateRevision));
      }
    }

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
        errorEl.hidden = !(startFailed || deliveryFailed);
        errorEl.textContent = deliveryFailed
          ? 'Не удалось подтвердить сохранение времени. Проверьте соединение и обновите состояние таймера.'
          : (startFailed
            ? 'Не удалось запустить таймер. Время решения пока не записывается.'
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
      if (doc && doc.body) {
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
      onState(currentState());
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

    function makeEvent(action, extra) {
      extra = extra || {};
      var eventSeq = nextSeq();
      var claimed = Object.prototype.hasOwnProperty.call(extra, 'claimed_ms')
        ? Math.max(0, Math.floor(Number(extra.claimed_ms) || 0))
        : currentClaimedMs();
      return {
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
      }).catch(function () {
        return { data: null, status: 0, retryable: true, networkError: true };
      });
    }

    function indicateDeliveryFailure() {
      deliveryFailed = true;
      if (!completed && status === 'running') {
        authoritative = false;
        status = 'auto_paused';
        runningSince = null;
        advanceStateRevision();
        syncTicker();
      }
      render();
    }

    function failQueuedEvents(data) {
      var failed = eventQueue.splice(0);
      var heartbeatFailed = failed.some(function (item) {
        return item.body && item.body.action === 'heartbeat';
      });
      indicateDeliveryFailure();
      if (heartbeatFailed && !completed && !manuallyPaused) {
        // The server may have expired the lease even though its response was
        // lost. Require an explicit resume instead of silently restarting it
        // when connectivity returns.
        resumeRequired = true;
        advanceStateRevision();
        render();
      }
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
            if (root.navigator && root.navigator.onLine === false) {
              item.stalled = true;
              eventQueueRunning = false;
              if (root.setTimeout && !item.expiryTimer) {
                item.expiryTimer = root.setTimeout(function () {
                  if (eventQueue[0] === item) failQueuedEvents({ ok: false, error: 'delivery_timeout' });
                }, Math.max(0, EVENT_MAX_AGE_MS - (Date.now() - item.createdAt)));
              }
              return;
            }
            item.attempts += 1;
            result = await requestEventOnce(item.body, item.keepalive);
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
                // The server expired this tab's lease. Do not silently leave
                // an editable board with a stopped clock: require a deliberate
                // resume, which establishes a fresh interval.
                resumeRequired = true;
                authoritative = false;
                runningSince = null;
                var serverElapsed = Number(data.accumulated_ms);
                if (isFinite(serverElapsed)) displayBaseMs = Math.max(0, serverElapsed);
                var serverCommitted = Number(data.committed_ms);
                if (isFinite(serverCommitted)) committedMs = Math.max(0, serverCommitted);
                advanceStateRevision();
                render();
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
          if (result) {
            if (root.navigator && root.navigator.onLine === false && item.attempts < item.maxAttempts) {
              item.stalled = true;
              eventQueueRunning = false;
              return;
            }
            failQueuedEvents(result.data);
            return;
          }
        }
      })().catch(function () {
        failQueuedEvents(null);
      }).finally(function () {
        eventQueueRunning = false;
        if (eventQueue.length && !(eventQueue[0] && eventQueue[0].stalled)) pumpEventQueue();
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
      if (
        destroyed || completed || manuallyPaused || resumeRequired || awaitingServer
        || startFailed || eventQueue.length || eventQueueRunning
      ) return;
      if (visibilityOf(doc) !== 'visible') return;
      if (startRetryTimer && root.clearTimeout) root.clearTimeout(startRetryTimer);
      startRetryTimer = null;
      started = true;
      awaitingServer = true;
      startAttempts += 1;
      if (!startEvent) {
        startEvent = makeEvent('start');
        startEventCreatedAt = Date.now();
      }
      // The outer start policy owns its six-attempt budget. Retrying in both
      // layers would multiply attempts and could create a retry burst.
      sendEvent(startEvent, false, 0, false).then(function (data) {
        awaitingServer = false;
        if (data && data.ok) {
          startAttempts = 0;
          startFailed = false;
          startEvent = null;
          startEventCreatedAt = 0;
          render();
          return;
        }
        // Do not let an interval heartbeat overtake a start retry after an
        // unacknowledged start. The server snapshot on acknowledgement will
        // restore the authoritative running state.
        authoritative = false;
        if (!completed && !manuallyPaused) status = 'auto_paused';
        advanceStateRevision();
        syncTicker();
        // A transient network/5xx/CSRF failure used to leave the clock frozen
        // at 0s forever. Retry a few times while the page is visible; a
        // gameplay-context error is deliberate and must be handled by reload.
        if (
          !destroyed
          && visibilityOf(doc) === 'visible'
          && !(data && data.reload_required)
          && startAttempts < 6
          && Date.now() - startEventCreatedAt < EVENT_MAX_AGE_MS
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
          if (popover) popover.hidden = false;
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

    function onVisible() {
      if (completed || manuallyPaused || resumeRequired) {
        render();
        return;
      }
      retryAfterConnectivity();
    }

    function pauseManual() {
      if (completed) return;
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
      manuallyPaused = false;
      resumeRequired = false;
      started = true;
      awaitingServer = true;
      advanceStateRevision();
      return post('resume').then(function (data) {
        awaitingServer = false;
        if (!data || !data.ok) {
          manuallyPaused = !!(data && (
            data.manually_paused || data.status === 'manually_paused'
          ));
          resumeRequired = !manuallyPaused;
          authoritative = false;
          status = manuallyPaused ? 'manually_paused' : 'auto_paused';
          advanceStateRevision();
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
      doc.addEventListener('visibilitychange', function () {
        if (visibilityOf(doc) === 'hidden') onHidden();
        else onVisible();
      });
      root.addEventListener && root.addEventListener('pagehide', onHidden);
      doc.addEventListener('submit', function (event) {
        var form = event && event.target;
        if (form && form.closest && form.closest('form[data-replay-submit]') && doc.documentElement) {
          doc.documentElement.setAttribute('data-replay-navigation', '1');
        }
      }, true);
      doc.addEventListener('interoves:daily-timing', function (ev) {
        if (ev && ev.detail) {
          var clientRevision = Number(ev.detail._client_revision);
          if (isFinite(clientRevision) && clientRevision < stateRevision) return;
          if (ev.detail.completed) markComplete(ev.detail);
          else applySnapshot(ev.detail, {
            replace: true,
            clientRevision: clientRevision,
          });
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
      boot: boot,
      destroy: function () {
        destroyed = true;
        awaitingServer = false;
        if (startRetryTimer && root.clearTimeout) root.clearTimeout(startRetryTimer);
        startRetryTimer = null;
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
    });
    controller.boot();
    return controller;
  }

  return {
    formatElapsed: formatElapsed,
    formatClock: formatClock,
    parseRetryAfter: parseRetryAfter,
    retryDelayMs: retryDelayMs,
    shouldRunLocally: shouldRunLocally,
    create: create,
    bindFromDocument: bindFromDocument,
    HEARTBEAT_MS: HEARTBEAT_MS,
  };
});
