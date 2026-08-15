'use strict';

var assert = require('assert');

function loadHelperWithWindow(fakeWindow) {
  global.window = fakeWindow;
  global.interovesAnalytics = undefined;
  delete require.cache[require.resolve('./yandex_metrika_helper.js')];
  return require('./yandex_metrika_helper.js');
}

function fakeTimers(target) {
  var timers = [];
  target.setTimeout = function (fn) {
    timers.push(fn);
    return timers.length;
  };
  target.clearTimeout = function () {};
  return timers;
}

function memoryStorage() {
  var values = Object.create(null);
  return {
    getItem: function (key) { return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : null; },
    setItem: function (key, value) { values[key] = String(value); },
    removeItem: function (key) { delete values[key]; },
  };
}

(function testSafeUntilCounterIsReady() {
  var calls = [];
  var fakeWindow = {
    interovesAnalyticsConfig: { yandexCounterId: 108320022 },
    ym: function () { calls.push([].slice.call(arguments)); },
  };
  fakeTimers(fakeWindow);
  var helper = loadHelperWithWindow(fakeWindow);
  assert.strictEqual(helper.trackYandexGoal('game_start', { game: 'ladder' }), false);
  helper.markYandexReady();
  assert.strictEqual(helper.trackYandexGoal('game_start', { game: 'ladder' }), true);
  assert.deepStrictEqual(calls[0], [
    108320022,
    'reachGoal',
    'game_start',
    { game: 'ladder' },
  ]);
})();

(function testOnceWaitsForReachGoalCallback() {
  var calls = [];
  var fakeWindow = {
    interovesAnalyticsConfig: { yandexCounterId: 108320022 },
    ym: function () { calls.push([].slice.call(arguments)); },
  };
  fakeTimers(fakeWindow);
  var helper = loadHelperWithWindow(fakeWindow);
  helper.markYandexReady();
  assert.strictEqual(helper.trackYandexGoalOnce('same-key', 'game_start', { game: 'replacement' }), true);
  assert.strictEqual(helper.trackYandexGoalOnce('same-key', 'game_start', { game: 'replacement' }), false);
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(typeof calls[0][4], 'function');
  calls[0][4]();
  assert.strictEqual(helper.trackYandexGoalOnce('same-key', 'game_start', { game: 'replacement' }), false);
  assert.strictEqual(calls.length, 1);
})();

(function testQueuedGoalFlushesOnlyAfterReadyEvent() {
  var calls = [];
  var listeners = {};
  var fakeWindow = {
    interovesAnalyticsConfig: { yandexCounterId: 108320022 },
    ym: function () { calls.push([].slice.call(arguments)); },
    addEventListener: function (name, fn) { listeners[name] = fn; },
  };
  fakeTimers(fakeWindow);
  var helper = loadHelperWithWindow(fakeWindow);
  assert.strictEqual(helper.trackYandexGoalOnce('late-key', 'game_start', { game: 'ladder' }), false);
  assert.strictEqual(calls.length, 0);
  listeners.yacounter108320022inited();
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0][2], 'game_start');
})();

(function testPendingGoalSurvivesReloadAndAckIsPostedAfterCallback() {
  var store = memoryStorage();
  var first = {
    interovesAnalyticsConfig: { yandexCounterId: 108320022 },
    localStorage: store,
  };
  fakeTimers(first);
  var helper = loadHelperWithWindow(first);
  helper.trackYandexGoalOnce(
    'persisted-key',
    'game_start',
    { game: 'ladder', game_id: '39' },
    { url: '/analytics/goals/ack/', token: 'signed-token' }
  );

  var calls = [];
  var fetches = [];
  var second = {
    interovesAnalyticsConfig: { yandexCounterId: 108320022 },
    localStorage: store,
    ym: function () { calls.push([].slice.call(arguments)); },
    fetch: function (url, options) {
      fetches.push([url, options]);
      return Promise.resolve({ ok: true });
    },
  };
  fakeTimers(second);
  helper = loadHelperWithWindow(second);
  helper.markYandexReady();
  assert.strictEqual(calls.length, 1);
  calls[0][4]();
  assert.strictEqual(fetches.length, 1);
  assert.strictEqual(fetches[0][0], '/analytics/goals/ack/');
  assert.strictEqual(fetches[0][1].body, 'token=signed-token');
})();

console.log('yandex_metrika_helper.test.js: ok');
