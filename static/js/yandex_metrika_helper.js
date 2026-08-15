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
  var MAX_STORED_AGE_MS = 14 * 24 * 60 * 60 * 1000;
  var STORAGE_KEY = 'interoves_pending_yandex_goals_v2';

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

  function scheduleRetry() {
    if (retryTimer || retryAttempts >= MAX_RETRY_ATTEMPTS || !global || typeof global.setTimeout !== 'function') return;
    retryTimer = global.setTimeout(function () {
      retryTimer = null;
      retryAttempts += 1;
      flushQueuedGoals();
      flushPendingAcks();
      if (hasPendingWork()) scheduleRetry();
    }, RETRY_DELAY_MS);
  }

  function rememberPendingGoal(key, goal, params, ack) {
    var existing = pendingGoals[key];
    pendingGoals[key] = {
      goal: goal,
      params: normalizeParams(params),
      ack: ack && typeof ack === 'object' ? ack : (existing && existing.ack) || null,
      createdAt: (existing && existing.createdAt) || nowMs(),
    };
    persistQueues();
    scheduleRetry();
  }

  function rememberPendingAck(key, ack) {
    if (!ack || !ack.url || !ack.token) return;
    pendingAcks[key] = {
      url: String(ack.url),
      token: String(ack.token),
      createdAt: nowMs(),
    };
    persistQueues();
  }

  function flushPendingAcks() {
    if (!global || typeof global.fetch !== 'function') return [];
    var dispatched = [];
    Object.keys(pendingAcks).forEach(function (key) {
      var ack = pendingAcks[key];
      if (!ack || ack.inFlight) return;
      ack.inFlight = true;
      dispatched.push(key);
      global.fetch(ack.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' },
        body: 'token=' + encodeURIComponent(ack.token),
        credentials: 'same-origin',
        keepalive: true,
      }).then(function (response) {
        if (!response || !response.ok) throw new Error('analytics ack failed');
        delete pendingAcks[key];
        persistQueues();
      }).catch(function () {
        if (pendingAcks[key]) delete pendingAcks[key].inFlight;
        persistQueues();
        scheduleRetry();
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
      persistQueues();
      flushPendingAcks();
    }
    function releaseForRetry() {
      if (finished) return;
      finished = true;
      delete inFlightKeys[key];
      scheduleRetry();
    }

    try {
      inFlightKeys[key] = true;
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
    }
    dispatched = dispatched.concat(flushQueuedGoals());
    flushPendingAcks();
    if (hasPendingWork()) scheduleRetry();
    return dispatched;
  }

  hydrateQueues();

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
  };
})(typeof window !== 'undefined' ? window : globalThis);

if (typeof module !== 'undefined' && module.exports) {
  module.exports = (typeof window !== 'undefined' && window.interovesAnalytics)
    || globalThis.interovesAnalytics;
}
