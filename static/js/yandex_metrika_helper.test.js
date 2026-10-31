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

(function testServerGoalsAreExposedToProductUiConsumers() {
  var received = [];
  var fakeWindow = {
    interovesAnalyticsConfig: { yandexCounterId: 108320022 },
    CustomEvent: function (type, options) {
      this.type = type;
      this.detail = options.detail;
    },
    dispatchEvent: function (event) { received.push(event); },
  };
  fakeTimers(fakeWindow);
  var helper = loadHelperWithWindow(fakeWindow);
  var goals = [{ goal: 'game_complete', params: { game: 'salad', game_id: '4' } }];

  helper.flushPendingGoals(goals);

  assert.strictEqual(received.length, 1);
  assert.strictEqual(received[0].type, 'interoves:analytics-goals');
  assert.deepStrictEqual(received[0].detail.goals, goals);
})();

(function testAuthoritativeGoalsDispatchBeforeConsumerFollowup() {
  var calls = [];
  var helper;
  var fakeWindow = {
    interovesAnalyticsConfig: { yandexCounterId: 108320022 },
    ym: function () { calls.push([].slice.call(arguments)); },
    CustomEvent: function (type, options) {
      this.type = type;
      this.detail = options.detail;
    },
    dispatchEvent: function (event) {
      if (event.type === 'interoves:analytics-goals') {
        helper.trackYandexGoalOnce(
          'onboarding-complete:test',
          'onboarding_first_game_complete',
          { game: 'salad' }
        );
      }
    },
  };
  fakeTimers(fakeWindow);
  helper = loadHelperWithWindow(fakeWindow);
  helper.markYandexReady();

  helper.flushPendingGoals([
    { key: 'start:test', goal: 'game_start', params: { game: 'salad', game_id: '4' } },
    { key: 'complete:test', goal: 'game_complete', params: { game: 'salad', game_id: '4' } },
  ]);

  assert.deepStrictEqual(calls.map(function (call) { return call[2]; }), [
    'game_start',
    'game_complete',
    'onboarding_first_game_complete',
  ]);
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

function ackWindow(store, respond, clock) {
  var fetches = [];
  var win = {
    interovesAnalyticsConfig: { yandexCounterId: 108320022 },
    localStorage: store,
    fetch: function (url, options) {
      fetches.push(options.body);
      return Promise.resolve(respond());
    },
  };
  fakeTimers(win);
  win.fetches = fetches;
  return win;
}

function storeWithAck() {
  var store = memoryStorage();
  store.setItem('interoves_pending_yandex_goals_v2', JSON.stringify({
    goals: {},
    acks: { k: { url: '/analytics/goals/ack/', token: 't', createdAt: Date.now() } },
  }));
  return store;
}

function flushPromises() {
  return new Promise(function (resolve) { setImmediate(resolve); });
}

async function testRejectedAckIsDroppedForGood() {
  var store = storeWithAck();
  var win = ackWindow(store, function () { return { ok: false, status: 400 }; });
  var helper = loadHelperWithWindow(win);
  helper.flushPendingGoals([]);
  await flushPromises();
  assert.strictEqual(win.fetches.length, 1);
  helper.flushPendingGoals([]);
  await flushPromises();
  assert.strictEqual(win.fetches.length, 1, 'a refused token is not resent');
  assert.deepStrictEqual(JSON.parse(store.getItem('interoves_pending_yandex_goals_v2')).acks, {});
}

async function testFailingAckBacksOffAcrossFlushesAndReloads() {
  var oldNow = Date.now;
  var now = oldNow();
  Date.now = function () { return now; };
  try {
    var store = storeWithAck();
    var unavailable = function () {
      return { ok: false, status: 503, headers: { get: function () { return '30'; } } };
    };
    var win = ackWindow(store, unavailable);
    var helper = loadHelperWithWindow(win);
    helper.flushPendingGoals([]);
    await flushPromises();
    assert.strictEqual(win.fetches.length, 1);
    // Gameplay responses and pageshow flush again right away: still waiting.
    helper.flushPendingGoals([]);
    await flushPromises();
    assert.strictEqual(win.fetches.length, 1, 'no resend before Retry-After');

    // A reload keeps the backoff.
    win = ackWindow(store, unavailable);
    helper = loadHelperWithWindow(win);
    helper.flushPendingGoals([]);
    await flushPromises();
    assert.strictEqual(win.fetches.length, 0);

    now += 31000;
    win = ackWindow(store, function () { return { ok: true, status: 200 }; });
    helper = loadHelperWithWindow(win);
    helper.flushPendingGoals([]);
    await flushPromises();
    assert.strictEqual(win.fetches.length, 1);
    assert.deepStrictEqual(JSON.parse(store.getItem('interoves_pending_yandex_goals_v2')).acks, {});
  } finally {
    Date.now = oldNow;
  }
}

(function testDeliveredGoalIsAnnounced() {
  var callbacks = [];
  var announced = [];
  var fakeWindow = {
    interovesAnalyticsConfig: { yandexCounterId: 108320022 },
    ym: function () { callbacks.push(arguments[4]); },
    CustomEvent: function (type, init) { this.type = type; this.detail = init.detail; },
    dispatchEvent: function (event) { announced.push(event); },
  };
  fakeTimers(fakeWindow);
  var helper = loadHelperWithWindow(fakeWindow);
  helper.markYandexReady();
  helper.trackYandexGoalOnce('switch-1', 'site_version_switch_clicked', { to: 'ru' });
  assert.strictEqual(announced.length, 0);
  callbacks[0]();
  assert.strictEqual(announced.length, 1);
  assert.strictEqual(announced[0].type, 'interoves:analytics-goal-delivered');
  assert.deepStrictEqual(announced[0].detail, { key: 'switch-1', goal: 'site_version_switch_clicked' });
})();

(async function runAsync() {
  await testRejectedAckIsDroppedForGood();
  await testFailingAckBacksOffAcrossFlushesAndReloads();
  console.log('yandex_metrika_helper.test.js: ok');
})().catch(function (err) {
  console.error(err);
  process.exit(1);
});

