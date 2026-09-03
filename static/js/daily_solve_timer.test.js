'use strict';

var assert = require('assert');
var timer = require('./daily_solve_timer.js');

(function testFormatElapsed() {
  assert.strictEqual(timer.formatElapsed(0), '0с');
  assert.strictEqual(timer.formatElapsed(9000), '9с');
  assert.strictEqual(timer.formatElapsed(226000), '3м 46с');
  assert.strictEqual(timer.formatElapsed(5564000), '1ч 32м 44с');
})();

(function testShouldRunLocally() {
  assert.strictEqual(timer.shouldRunLocally({
    completed: false,
    manually_paused: false,
    is_authoritative: true,
    status: 'running',
  }, 'visible'), true);
  assert.strictEqual(timer.shouldRunLocally({
    completed: false,
    manually_paused: false,
    is_authoritative: true,
    status: 'running',
  }, 'hidden'), false);
  assert.strictEqual(timer.shouldRunLocally({
    completed: false,
    manually_paused: true,
    is_authoritative: true,
    status: 'manually_paused',
  }, 'visible'), false);
  assert.strictEqual(timer.shouldRunLocally({
    completed: true,
    manually_paused: false,
    is_authoritative: false,
    status: 'completed',
  }, 'visible'), false);
})();

function memoryStorage() {
  var values = Object.create(null);
  return {
    getItem: function (key) { return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : null; },
    setItem: function (key, value) { values[key] = String(value); },
    removeItem: function (key) { delete values[key]; },
  };
}

function fakeClock(start) {
  var t = start || 0;
  return {
    now: function () { return t; },
    advance: function (ms) { t += ms; },
  };
}

(function testLocalTickDoesNotUseWallClockGap() {
  var clock = fakeClock(1000);
  var posts = [];
  var ctrl = timer.create({
    url: '/ladder/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return 'anon'; },
    getCsrf: function () { return 'csrf'; },
    clock: clock,
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function (url, init) {
      posts.push({ url: url, init: init });
      return Promise.resolve({ json: function () { return Promise.resolve({ ok: true, status: 'running', is_authoritative: true, accumulated_ms: 0, committed_ms: 0, exists: true }); } });
    },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'running', is_authoritative: true, accumulated_ms: 0, committed_ms: 0, exists: true },
  });
  clock.advance(260000);
  assert.strictEqual(timer.formatElapsed(ctrl.displayedMs()), '4м 20с');
  ctrl.onHidden();
  clock.advance(3 * 3600 * 1000);
  assert.strictEqual(timer.formatElapsed(ctrl.displayedMs()), '4м 20с');
  ctrl.destroy();
})();

(function testManualPauseStopsTickAcrossReloadBootstrap() {
  var clock = fakeClock(0);
  var ctrl = timer.create({
    url: '/ladder/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return 'anon'; },
    getCsrf: function () { return ''; },
    clock: clock,
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({ ok: true }); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'manually_paused', manually_paused: true, accumulated_ms: 5000, committed_ms: 5000, exists: true, is_authoritative: false },
  });
  clock.advance(60000);
  assert.strictEqual(ctrl.displayedMs(), 5000);
  ctrl.startIfAllowed();
  clock.advance(60000);
  assert.strictEqual(ctrl.displayedMs(), 5000);
  ctrl.destroy();
})();

(function testCompletedDoesNotTick() {
  var clock = fakeClock(0);
  var ctrl = timer.create({
    url: '/ladder/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return ''; },
    getCsrf: function () { return ''; },
    clock: clock,
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({}); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'completed', completed: true, accumulated_ms: 8000, frozen_ms: 8000, exists: true },
    solved: true,
  });
  clock.advance(120000);
  assert.strictEqual(ctrl.displayedMs(), 8000);
  ctrl.destroy();
})();

(function testReloadKeepsSessionSeq() {
  var store = memoryStorage();
  var clock = fakeClock(0);
  var first = timer.create({
    url: '/ladder/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return 'anon'; },
    getCsrf: function () { return ''; },
    clock: clock,
    storage: store,
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({ ok: true }); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'running', is_authoritative: true, accumulated_ms: 0, exists: true },
  });
  first.onHidden();
  first.destroy();
  var second = timer.create({
    url: '/ladder/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return 'anon'; },
    getCsrf: function () { return ''; },
    clock: clock,
    storage: store,
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({ ok: true }); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'auto_paused', accumulated_ms: 5000, committed_ms: 5000, exists: true },
  });
  assert.ok(store.getItem('session:/ladder/1/timing/'));
  assert.strictEqual(store.getItem('session:/ladder/1/timing/:seq'), '1');
  second.destroy();
})();

(function testManualPauseIgnoresRunningSnapshot() {
  var clock = fakeClock(0);
  var ctrl = timer.create({
    url: '/ladder/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return 'anon'; },
    getCsrf: function () { return ''; },
    clock: clock,
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({ ok: true }); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'running', is_authoritative: true, accumulated_ms: 4000, committed_ms: 4000, exists: true },
  });
  ctrl.pauseManual();
  assert.strictEqual(ctrl.state().status, 'manually_paused');
  ctrl.applySnapshot({
    ok: true,
    status: 'running',
    is_authoritative: true,
    accumulated_ms: 20000,
    committed_ms: 20000,
    exists: true,
  }, { replace: true });
  assert.strictEqual(ctrl.state().status, 'manually_paused');
  assert.strictEqual(ctrl.displayedMs(), 4000);
  clock.advance(30000);
  assert.strictEqual(ctrl.displayedMs(), 4000);
  ctrl.destroy();
})();

(function testHiddenIgnoresRunningSnapshot() {
  var clock = fakeClock(0);
  var doc = { visibilityState: 'visible', addEventListener: function () {} };
  var ctrl = timer.create({
    url: '/ladder/1/timing/',
    document: doc,
    getAnonKey: function () { return 'anon'; },
    getCsrf: function () { return ''; },
    clock: clock,
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({ ok: true }); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'running', is_authoritative: true, accumulated_ms: 1000, exists: true },
  });
  doc.visibilityState = 'hidden';
  ctrl.onHidden();
  ctrl.applySnapshot({
    status: 'running',
    is_authoritative: true,
    accumulated_ms: 8000,
    exists: true,
  }, { replace: true });
  assert.strictEqual(ctrl.state().status, 'auto_paused');
  assert.strictEqual(ctrl.displayedMs(), 1000);
  ctrl.destroy();
})();

(function testDestroyedIgnoresSnapshot() {
  var ctrl = timer.create({
    url: '/ladder/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return ''; },
    getCsrf: function () { return ''; },
    clock: fakeClock(0),
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({}); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'running', is_authoritative: true, accumulated_ms: 2000, exists: true },
  });
  ctrl.destroy();
  ctrl.applySnapshot({
    status: 'running',
    is_authoritative: true,
    accumulated_ms: 99999,
    exists: true,
  }, { replace: true });
  assert.strictEqual(ctrl.displayedMs(), 2000);
})();

console.log('daily_solve_timer tests ok');
process.exit(0);
