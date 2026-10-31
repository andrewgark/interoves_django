(function (global) {
  'use strict';

  var sentKeys = Object.create(null);
  var pendingGoals = Object.create(null);
  var pendingAcks = Object.create(null);
  var inFlightKeys = Object.create(null);
  var retryTimer = null;
  var retryAttempts = 0;
  var yandexReady = false;
  var RETRY_DELAY_MS = 1000;
  var IN_FLIGHT_TIMEOUT_MS = 10000;
  var MAX_RETRY_ATTEMPTS = 20;
  // Acks back off per token, across page loads: every load, pageshow and
  // gameplay response flushes them, so a shared 1 s timer did not limit
  // anything and one browser sent thousands of acks a day.
  var ACK_BASE_DELAY_MS = 2000;
  var ACK_MAX_DELAY_MS = 10 * 60 * 1000;
  var ACK_MAX_ATTEMPTS = 12;
  var MAX_STORED_AGE_MS = 14 * 24 * 60 * 60 * 1000;
  var STORAGE_KEY = 'interoves_pending_yandex_goals_v2';
  var DEBUG_STORAGE_KEY = 'interoves_analytics_debug';

  function getConfig() {
    return global && global.interovesAnalyticsConfig ? global.interovesAnalyticsConfig : {};
  }

  function getCounterId() {
    var raw = getConfig().yandexCounterId;
    var id = Number(raw);
    return isFinite(id) && id > 0 ? id : null;
  }

  function normalizeParams(params) {
    return params && typeof params === 'object' ? params : {};
  }

  function nowMs() {
    return Date.now ? Date.now() : new Date().getTime();
  }

  function storage() {
    try { return global && global.localStorage ? global.localStorage : null; }
    catch (e) { return null; }
  }

  function debugEnabled() {
    var target = storage();
    var queryValue = null;
    try {
      if (global && global.location && typeof global.URL === 'function') {
        queryValue = new global.URL(global.location.href).searchParams.get('analytics_debug');
      }
    } catch (e) {}
    if (queryValue !== null && target) {
      try {
        if (queryValue === '1' || queryValue === 'true') target.setItem(DEBUG_STORAGE_KEY, '1');
        else if (queryValue === '0' || queryValue === 'false') target.removeItem(DEBUG_STORAGE_KEY);
      } catch (e) {}
    }
    if (queryValue === '1' || queryValue === 'true') return true;
    if (queryValue === '0' || queryValue === 'false') return false;
    try { return !!(target && target.getItem(DEBUG_STORAGE_KEY) === '1'); }
    catch (e) { return false; }
  }

  function onboardingDebugContext() {
    var target = storage();
    if (!target) return null;
    try {
      var value = JSON.parse(target.getItem('interoves_onboarding_v2') || 'null');
      if (!value || typeof value !== 'object') return null;
      return {
        stage: String(value.stage || ''),
        selected_game: String(value.selectedGame || ''),
        first_game: String(value.firstGame || ''),
        first_game_id: String(value.firstGameId || ''),
        recommended: !!value.recommended,
      };
    } catch (e) { return null; }
  }

  function debugLog(phase, goal, params, key) {
    if (!debugEnabled() || !global || !global.console || typeof global.console.debug !== 'function') return;
    params = normalizeParams(params);
    var path = global.location && global.location.pathname ? global.location.pathname : '';
    var timestamp = new Date().toISOString();
    var details = {
      phase: String(phase || ''),
      event: String(goal || ''),
      pathname: path,
      game: String(params.game || ''),
      game_id: String(params.game_id || ''),
      onboarding: onboardingDebugContext(),
    };
    if (key) details.key = String(key);
    global.console.debug(
      '[interoves analytics] ' + timestamp + ' ' + String(goal || phase || '') + ' ' + path,
      details
    );
  }

  function persistQueues() {
    var target = storage();
    if (!target) return;
    try {
      target.setItem(STORAGE_KEY, JSON.stringify({
        goals: pendingGoals,
        acks: pendingAcks,
      }));
    } catch (e) {}
  }

  function freshTimestamp(value) {
    var ts = Number(value || 0);
    return ts > 0 && nowMs() - ts <= MAX_STORED_AGE_MS;
  }

  function hydrateQueues() {
    var target = storage();
    if (!target) return;
    try {
      var parsed = JSON.parse(target.getItem(STORAGE_KEY) || '{}');
      var goals = parsed && parsed.goals && typeof parsed.goals === 'object' ? parsed.goals : {};
      var acks = parsed && parsed.acks && typeof parsed.acks === 'object' ? parsed.acks : {};
      Object.keys(goals).forEach(function (key) {
        var item = goals[key];
        if (!item || !item.goal || !freshTimestamp(item.createdAt)) return;
        pendingGoals[key] = item;
      });
      Object.keys(acks).forEach(function (key) {
        var item = acks[key];
        if (!item || !item.url || !item.token || !freshTimestamp(item.createdAt)) return;
        pendingAcks[key] = {
          url: String(item.url),
          token: String(item.token),
          createdAt: Number(item.createdAt),
          attempts: Number(item.attempts) || 0,
          nextAttemptAt: Number(item.nextAttemptAt) || 0,
        };
        // reachGoal already called its delivery callback; only the same-origin
        // acknowledgement remains, so do not send the Yandex goal again.
        sentKeys[key] = true;
      });
    } catch (e) {}
  }

  function hasPendingWork() {
    return Object.keys(pendingGoals).length > 0 || Object.keys(pendingAcks).length > 0;
  }

  function scheduleRetry(delayMs) {
    if (retryTimer || retryAttempts >= MAX_RETRY_ATTEMPTS || !global || typeof global.setTimeout !== 'function') return;
    retryTimer = global.setTimeout(function () {
      retryTimer = null;
      retryAttempts += 1;
      flushQueuedGoals();
      flushPendingAcks();
      if (hasPendingWork()) scheduleRetry(nextAckDelayMs());
    }, delayMs || RETRY_DELAY_MS);
  }

  // Wait until the earliest ack is due, so the retry timer does not spend
  // its attempts on acks that are still backing off.
  function nextAckDelayMs() {
    if (Object.keys(pendingGoals).length) return RETRY_DELAY_MS;
    var now = nowMs();
    var earliest = null;
    Object.keys(pendingAcks).forEach(function (key) {
      var due = Number(pendingAcks[key].nextAttemptAt) || 0;
      if (earliest === null || due < earliest) earliest = due;
    });
    return earliest === null ? RETRY_DELAY_MS : Math.max(RETRY_DELAY_MS, earliest - now);
  }

  function retryAfterMs(response) {
    var raw = response && response.headers && typeof response.headers.get === 'function'
      ? response.headers.get('Retry-After')
      : null;
    var seconds = Number(raw);
    return isFinite(seconds) && seconds > 0 ? seconds * 1000 : 0;
  }

  // 408 and 429 are worth retrying; any other 4xx (a bad or expired token)
  // will be refused again, so the ack is dropped instead of resent forever.
  function isPermanentFailure(status) {
    return status >= 400 && status < 500 && status !== 408 && status !== 429;
  }

  function rememberPendingGoal(key, goal, params, ack) {
    var existing = pendingGoals[key];
    pendingGoals[key] = {
      goal: goal,
      params: normalizeParams(params),
      ack: ack && typeof ack === 'object' ? ack : (existing && existing.ack) || null,
      createdAt: (existing && existing.createdAt) || nowMs(),
    };
    debugLog('queued', goal, params, key);
    persistQueues();
    scheduleRetry();
  }

  function rememberPendingAck(key, ack) {
    if (!ack || !ack.url || !ack.token) return;
    pendingAcks[key] = {
      url: String(ack.url),
      token: String(ack.token),
      createdAt: nowMs(),
      attempts: 0,
      nextAttemptAt: 0,
    };
    persistQueues();
  }

  function backOffAck(key, minDelayMs) {
    var ack = pendingAcks[key];
    if (!ack) return;
    delete ack.inFlight;
    ack.attempts = (Number(ack.attempts) || 0) + 1;
    if (ack.attempts >= ACK_MAX_ATTEMPTS) {
      delete pendingAcks[key];
    } else {
      var delay = Math.min(ACK_MAX_DELAY_MS, ACK_BASE_DELAY_MS * Math.pow(2, ack.attempts - 1));
      ack.nextAttemptAt = nowMs() + Math.max(delay, minDelayMs || 0);
    }
    persistQueues();
    if (hasPendingWork()) scheduleRetry(nextAckDelayMs());
  }

  function flushPendingAcks() {
    if (!global || typeof global.fetch !== 'function') return [];
    var dispatched = [];
    var now = nowMs();
    Object.keys(pendingAcks).forEach(function (key) {
      var ack = pendingAcks[key];
      if (!ack || ack.inFlight || (Number(ack.nextAttemptAt) || 0) > now) return;
      ack.inFlight = true;
      dispatched.push(key);
      global.fetch(ack.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' },
        body: 'token=' + encodeURIComponent(ack.token),
        credentials: 'same-origin',
        keepalive: true,
      }).then(function (response) {
        var status = response ? Number(response.status) || 0 : 0;
        if (response && (response.ok || isPermanentFailure(status))) {
          delete pendingAcks[key];
          persistQueues();
          return;
        }
        backOffAck(key, retryAfterMs(response));
      }, function () {
        backOffAck(key, 0);
      });
    });
    return dispatched;
  }

  function isYandexReady() {
    return !!yandexReady;
  }

  function markYandexReady() {
    yandexReady = true;
    retryAttempts = 0;
    var sent = flushQueuedGoals();
    flushPendingAcks();
    if (hasPendingWork()) scheduleRetry();
    return sent;
  }

  function trackYandexGoal(goal, params) {
    if (!global || !goal || !isYandexReady()) return false;
    var counterId = getCounterId();
    if (!counterId || typeof global.ym !== 'function') return false;
    try {
      debugLog('dispatch', goal, params, '');
      global.ym(counterId, 'reachGoal', goal, normalizeParams(params));
      return true;
    } catch (e) {
      return false;
    }
  }

  function dispatchPendingGoal(key) {
    var payload = pendingGoals[key];
    if (!payload || sentKeys[key] || inFlightKeys[key] || !isYandexReady()) return false;
    var counterId = getCounterId();
    if (!counterId || typeof global.ym !== 'function') return false;

    var finished = false;
    var timeoutId = null;
    function finishDelivered() {
      if (finished) return;
      finished = true;
      if (timeoutId && global && typeof global.clearTimeout === 'function') global.clearTimeout(timeoutId);
      delete inFlightKeys[key];
      sentKeys[key] = true;
      delete pendingGoals[key];
      if (payload.ack) rememberPendingAck(key, payload.ack);
      debugLog('delivered', payload.goal, payload.params, key);
      persistQueues();
      flushPendingAcks();
      notifyGoalDelivered(key, payload.goal);
    }
    function releaseForRetry() {
      if (finished) return;
      finished = true;
      delete inFlightKeys[key];
      debugLog('retry', payload.goal, payload.params, key);
      scheduleRetry();
    }

    try {
      inFlightKeys[key] = true;
      debugLog('dispatch', payload.goal, payload.params, key);
      global.ym(
        counterId,
        'reachGoal',
        payload.goal,
        normalizeParams(payload.params),
        finishDelivered
      );
      if (global && typeof global.setTimeout === 'function') {
        timeoutId = global.setTimeout(releaseForRetry, IN_FLIGHT_TIMEOUT_MS);
      }
      return true;
    } catch (e) {
      delete inFlightKeys[key];
      scheduleRetry();
      return false;
    }
  }

  function flushQueuedGoals() {
    var dispatched = [];
    Object.keys(pendingGoals).forEach(function (key) {
      if (sentKeys[key]) {
        delete pendingGoals[key];
        return;
      }
      if (dispatchPendingGoal(key)) dispatched.push(key);
    });
    persistQueues();
    return dispatched;
  }

  function trackYandexGoalOnce(key, goal, params, ack) {
    var dedupeKey = String(key || goal || '');
    if (!dedupeKey) return trackYandexGoal(goal, params);
    if (sentKeys[dedupeKey]) return false;
    rememberPendingGoal(dedupeKey, goal, params, ack);
    return dispatchPendingGoal(dedupeKey);
  }

  // Lets a page hold a cross-site navigation until its goal is delivered:
  // a goal still queued in this origin's localStorage may never be retried.
  function notifyGoalDelivered(key, goal) {
    if (!global || typeof global.dispatchEvent !== 'function' || typeof global.CustomEvent !== 'function') return;
    try {
      global.dispatchEvent(new global.CustomEvent('interoves:analytics-goal-delivered', {
        detail: { key: key, goal: goal }
      }));
    } catch (e) {}
  }

  function notifyGoalConsumers(goals) {
    if (!global || !goals || !goals.length || typeof global.dispatchEvent !== 'function') return;
    try {
      global.dispatchEvent(new global.CustomEvent('interoves:analytics-goals', {
        detail: { goals: goals }
      }));
    } catch (e) {}
  }

  function flushPendingGoals(goals) {
    var dispatched = [];
    if (goals && goals.length) {
      goals.forEach(function (goal) {
        if (!goal || typeof goal !== 'object') return;
        var key = goal.key || goal.goal;
        if (trackYandexGoalOnce(key, goal.goal, goal.params || {}, goal.ack || null)) {
          dispatched.push(String(key));
        }
      });
      // Consumers can derive follow-up goals (for example onboarding completion),
      // so notify only after the authoritative gameplay goals were queued/sent.
      notifyGoalConsumers(goals);
    }
    dispatched = dispatched.concat(flushQueuedGoals());
    flushPendingAcks();
    if (hasPendingWork()) scheduleRetry();
    return dispatched;
  }

  hydrateQueues();
  debugLog('page', 'page', {}, '');

  if (global && typeof global.addEventListener === 'function') {
    var counterId = getCounterId();
    var readyEventName = counterId ? 'yacounter' + counterId + 'inited' : '';
    if (readyEventName) {
      global.addEventListener(readyEventName, markYandexReady);
      if (global.document && typeof global.document.addEventListener === 'function') {
        global.document.addEventListener(readyEventName, markYandexReady);
      }
    }
    global.addEventListener('load', function () {
      flushQueuedGoals();
      flushPendingAcks();
    });
    global.addEventListener('pageshow', function () {
      flushQueuedGoals();
      flushPendingAcks();
    });
  }
  if (hasPendingWork()) scheduleRetry();

  global.interovesAnalytics = {
    getCounterId: getCounterId,
    isYandexReady: isYandexReady,
    markYandexReady: markYandexReady,
    trackYandexGoal: trackYandexGoal,
    trackYandexGoalOnce: trackYandexGoalOnce,
    flushPendingGoals: flushPendingGoals,
    debugLog: debugLog,
  };
})(typeof window !== 'undefined' ? window : globalThis);

if (typeof module !== 'undefined' && module.exports) {
  module.exports = (typeof window !== 'undefined' && window.interovesAnalytics)
    || globalThis.interovesAnalytics;
}
