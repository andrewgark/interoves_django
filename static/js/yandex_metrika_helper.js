(function (global) {
  'use strict';

  var sentKeys = Object.create(null);
  var pendingGoals = Object.create(null);
  var retryTimer = null;
  var RETRY_DELAY_MS = 1000;
  var MAX_RETRY_ATTEMPTS = 20;

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

  function scheduleRetry() {
    if (retryTimer || !global || typeof global.setTimeout !== 'function') return;
    retryTimer = global.setTimeout(function () {
      retryTimer = null;
      flushQueuedGoals();
      if (hasPendingGoals()) scheduleRetry();
    }, RETRY_DELAY_MS);
  }

  function hasPendingGoals() {
    return Object.keys(pendingGoals).length > 0;
  }

  function rememberPendingGoal(key, goal, params) {
    var existing = pendingGoals[key] || {};
    pendingGoals[key] = {
      goal: goal,
      params: normalizeParams(params),
      attempts: (existing.attempts || 0) + 1,
    };
    if (pendingGoals[key].attempts < MAX_RETRY_ATTEMPTS) scheduleRetry();
  }

  function flushQueuedGoals() {
    var sent = [];
    Object.keys(pendingGoals).forEach(function (key) {
      var payload = pendingGoals[key];
      if (!payload || sentKeys[key]) {
        delete pendingGoals[key];
        return;
      }
      if (trackYandexGoal(payload.goal, payload.params)) {
        sentKeys[key] = true;
        delete pendingGoals[key];
        sent.push(key);
      }
    });
    return sent;
  }

  function trackYandexGoal(goal, params) {
    if (!global || !goal) return false;
    var counterId = getCounterId();
    if (!counterId || typeof global.ym !== 'function') return false;
    try {
      global.ym(counterId, 'reachGoal', goal, normalizeParams(params));
      return true;
    } catch (e) {
      return false;
    }
  }

  function trackYandexGoalOnce(key, goal, params) {
    var dedupeKey = String(key || goal || '');
    if (!dedupeKey) return trackYandexGoal(goal, params);
    if (sentKeys[dedupeKey]) return false;
    if (trackYandexGoal(goal, params)) {
      sentKeys[dedupeKey] = true;
      delete pendingGoals[dedupeKey];
      return true;
    }
    rememberPendingGoal(dedupeKey, goal, params);
    return false;
  }

  function flushPendingGoals(goals) {
    var sent = [];
    if (goals && goals.length) {
      goals.forEach(function (goal) {
        if (!goal || typeof goal !== 'object') return;
        var key = goal.key || goal.goal;
        if (trackYandexGoalOnce(key, goal.goal, goal.params || {})) sent.push(String(key));
      });
    }
    sent = sent.concat(flushQueuedGoals());
    if (hasPendingGoals()) scheduleRetry();
    return sent;
  }

  if (global && typeof global.addEventListener === 'function') {
    global.addEventListener('load', flushQueuedGoals);
    global.addEventListener('pageshow', flushQueuedGoals);
  }

  global.interovesAnalytics = {
    getCounterId: getCounterId,
    trackYandexGoal: trackYandexGoal,
    trackYandexGoalOnce: trackYandexGoalOnce,
    flushPendingGoals: flushPendingGoals,
  };
})(typeof window !== 'undefined' ? window : globalThis);

if (typeof module !== 'undefined' && module.exports) {
  module.exports = (typeof window !== 'undefined' && window.interovesAnalytics)
    || globalThis.interovesAnalytics;
}
