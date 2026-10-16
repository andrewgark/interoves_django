'use strict';

var assert = require('assert');
var timer = require('./daily_solve_timer.js');

(function testFormatElapsed() {
  assert.strictEqual(timer.formatElapsed(0), '0с');
  assert.strictEqual(timer.formatElapsed(9000), '9с');
  assert.strictEqual(timer.formatElapsed(226000), '3м 46с');
  assert.strictEqual(timer.formatElapsed(5564000), '1ч 32м 44с');
})();

(function testFormatClockForCompactTimerControl() {
  assert.strictEqual(timer.formatClock(0), '00:00');
  assert.strictEqual(timer.formatClock(54000), '00:54');
  assert.strictEqual(timer.formatClock(3661000), '01:01:01');
})();

(function testRetryAfterMinimumJitterAndBounds() {
  assert.strictEqual(timer.parseRetryAfter('2', 1000), 2000);
  assert.strictEqual(timer.parseRetryAfter('Thu, 01 Jan 1970 00:00:05 GMT', 1000), 4000);
  assert.strictEqual(timer.parseRetryAfter('not a date', 1000), 0);
  assert.strictEqual(timer.parseRetryAfter('999999', 1000), 8000);
  assert.strictEqual(timer.retryDelayMs(2000, 0, 0.5), 2125);
  assert.strictEqual(timer.retryDelayMs(99999, 3, 1), 10000);
})();

(function testHoverTooltipUpdatesItsValueWithoutReplacingTheTooltip() {
  var tooltipValue = { textContent: '' };
  var toggleBtn = {
    setAttribute: function () {},
    title: 'must not be rewritten',
  };
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
    toggleBtn: toggleBtn,
    tooltipValue: tooltipValue,
    bootstrap: { status: 'running', is_authoritative: true, accumulated_ms: 61000, exists: true },
  });
  assert.strictEqual(tooltipValue.textContent, '01:01');
  assert.strictEqual(toggleBtn.title, 'must not be rewritten');
  ctrl.destroy();
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

(function testCompletedHidesTimerAndPause() {
  var rootEl = { hidden: false, classList: { toggle: function () {} } };
  var pauseBtn = { hidden: false };
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
    root: rootEl,
    pauseBtn: pauseBtn,
    bootstrap: { status: 'completed', completed: true, accumulated_ms: 8000, frozen_ms: 8000, exists: true },
    solved: true,
  });
  assert.strictEqual(rootEl.hidden, true);
  assert.strictEqual(pauseBtn.hidden, true);
  ctrl.destroy();
})();

(function testSolvedPageKeepsTimerHiddenIfSnapshotIsStillOpen() {
  var rootEl = { hidden: false, classList: { toggle: function () {} } };
  var pauseBtn = { hidden: false };
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
    root: rootEl,
    pauseBtn: pauseBtn,
    solved: true,
    bootstrap: { status: 'auto_paused', completed: false, accumulated_ms: 0, exists: false },
  });
  assert.strictEqual(rootEl.hidden, true);
  assert.strictEqual(pauseBtn.hidden, true);
  ctrl.applySnapshot({
    status: 'auto_paused',
    completed: false,
    accumulated_ms: 0,
    exists: false,
  }, { replace: true });
  assert.strictEqual(ctrl.state().completed, true);
  assert.strictEqual(rootEl.hidden, true);
  assert.strictEqual(pauseBtn.hidden, true);
  ctrl.startIfAllowed();
  assert.strictEqual(ctrl.state().status, 'completed');
  ctrl.destroy();
})();

(function testMarkCompleteHidesTimerAndPause() {
  var rootEl = { hidden: true, classList: { toggle: function () {} } };
  var pauseBtn = { hidden: false };
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
    root: rootEl,
    pauseBtn: pauseBtn,
    bootstrap: { status: 'running', is_authoritative: true, accumulated_ms: 4000, exists: true },
  });
  assert.strictEqual(rootEl.hidden, false);
  assert.strictEqual(pauseBtn.hidden, false);
  ctrl.markComplete({ status: 'completed', completed: true, accumulated_ms: 4000, frozen_ms: 4000, exists: true });
  assert.strictEqual(rootEl.hidden, true);
  assert.strictEqual(pauseBtn.hidden, true);
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

(function testWindowOpenGetsNewSessionDespiteCopiedSessionStorage() {
  var openerStorage = memoryStorage();
  var openerWindow = { name: '' };
  var first = timer.create({
    url: '/ladder/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    windowIdentity: openerWindow,
    storage: openerStorage,
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({}); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'auto_paused', exists: false },
  });
  var openerSession = openerStorage.getItem('session:/ladder/1/timing/');
  first.destroy();

  // A real window.open() child may start with a copy of sessionStorage, but
  // it has no copied window.name marker.
  var childStorage = memoryStorage();
  childStorage.setItem('session:/ladder/1/timing/', openerSession);
  var second = timer.create({
    url: '/ladder/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    windowIdentity: { name: '' },
    storage: childStorage,
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({}); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'auto_paused', exists: false },
  });
  assert.notStrictEqual(childStorage.getItem('session:/ladder/1/timing/'), openerSession);
  second.destroy();
})();

function parsePostBody(init) {
  var type = (init && init.headers && init.headers['Content-Type']) || '';
  if (String(type).indexOf('json') >= 0) return JSON.parse(init.body);
  var out = {};
  new URLSearchParams(init.body).forEach(function (value, key) { out[key] = value; });
  return out;
}

(function testPauseSendsOpenIntervalAndDoesNotJumpDown() {
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
      posts.push(parsePostBody(init));
      return Promise.resolve({
        json: function () {
          return Promise.resolve({
            ok: true,
            status: 'manually_paused',
            manually_paused: true,
            accumulated_ms: 15000,
            committed_ms: 15000,
            exists: true,
            is_authoritative: false,
          });
        },
      });
    },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    bootstrap: { status: 'running', is_authoritative: true, accumulated_ms: 0, committed_ms: 0, exists: true },
  });
  clock.advance(260000);
  ctrl.pauseManual();
  var pause = posts.filter(function (body) { return body.action === 'pause'; })[0];
  assert.ok(pause, 'pause request was sent');
  assert.ok(Number(pause.claimed_ms) >= 250000, pause.claimed_ms);
  assert.ok(Date.parse(pause.client_occurred_at) > 0, pause.client_occurred_at);
  assert.strictEqual(timer.formatElapsed(ctrl.displayedMs()), '4м 20с');
  ctrl.destroy();
})();

(function testHiddenSendsOpenIntervalClaimedMs() {
  var clock = fakeClock(0);
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
      posts.push(parsePostBody(init));
      return Promise.resolve({
        json: function () {
          return Promise.resolve({
            ok: true,
            status: 'auto_paused',
            accumulated_ms: 15000,
            committed_ms: 15000,
            exists: true,
            is_authoritative: false,
          });
        },
      });
    },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    bootstrap: { status: 'running', is_authoritative: true, accumulated_ms: 0, committed_ms: 0, exists: true },
  });
  clock.advance(90000);
  ctrl.onHidden();
  var hidden = posts.filter(function (body) { return body.action === 'auto_pause'; })[0];
  assert.ok(hidden);
  assert.ok(Number(hidden.claimed_ms) >= 80000, hidden.claimed_ms);
  assert.strictEqual(timer.formatElapsed(ctrl.displayedMs()), '1м 30с');
  ctrl.destroy();
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

(function testOlderMutationResponseCannotRollBackNewerTimerState() {
  var ctrl = timer.create({
    url: '/ladder/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return 'anon'; },
    getCsrf: function () { return ''; },
    clock: fakeClock(0),
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({ ok: true }); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'auto_paused', is_authoritative: false, accumulated_ms: 4000, exists: true },
  });

  ctrl.applySnapshot({
    status: 'running', is_authoritative: true, accumulated_ms: 6000, exists: true,
  }, { requestSeq: 8 });
  ctrl.applySnapshot({
    status: 'manually_paused', manually_paused: true, accumulated_ms: 5000, exists: true,
  }, { requestSeq: 7 });

  assert.strictEqual(ctrl.state().status, 'running');
  assert.strictEqual(ctrl.state().manually_paused, false);
  assert.strictEqual(ctrl.displayedMs(), 6000);
  ctrl.destroy();
})();

(function testAuthoritativeZeroCompletionReplacesLocalDisplay() {
  var ctrl = timer.create({
    url: '/ladder/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return 'anon'; },
    getCsrf: function () { return ''; },
    clock: fakeClock(0),
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({ ok: true }); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'running', is_authoritative: true, accumulated_ms: 7000, exists: true },
  });

  ctrl.applySnapshot({
    status: 'completed', completed: true, frozen_ms: 0, accumulated_ms: 0, exists: true,
  }, { requestSeq: 1 });

  assert.strictEqual(ctrl.displayedMs(), 0);
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

(async function testFailedResumeRestoresManualPause() {
  var ctrl = timer.create({
    url: '/ladder/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return 'anon'; },
    getCsrf: function () { return ''; },
    clock: fakeClock(0),
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function () {
      return Promise.resolve({
        status: 409,
        json: function () { return Promise.resolve({ ok: false }); },
      });
    },
    listenDocument: false,
    enableHeartbeat: false,
    bootstrap: {
      status: 'manually_paused',
      manually_paused: true,
      is_authoritative: false,
      accumulated_ms: 4000,
      exists: true,
    },
  });
  await ctrl.resumeManual();
  assert.strictEqual(ctrl.state().status, 'manually_paused');
  assert.strictEqual(ctrl.state().manually_paused, true);
  ctrl.destroy();
})().catch(function (error) { console.error(error); process.exitCode = 1; });

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

(function testPersonalModeDoesNotUseForeignTabBroadcast() {
  var ctrl = timer.create({
    url: '/ladder/personal/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return ''; },
    getCsrf: function () { return ''; },
    clock: fakeClock(0),
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({}); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    bootstrap: { status: 'running', is_authoritative: true, accumulated_ms: 0, exists: true },
  });
  assert.strictEqual(ctrl.state().is_authoritative, true);
  ctrl.destroy();
})();

(async function testStartFailureIsVisibleAfterRetriesAndClearsAfterRecovery() {
  var originalSetTimeout = global.setTimeout;
  var originalClearTimeout = global.clearTimeout;
  var pendingTimers = [];
  var fail = true;
  var starts = [];
  var errorEl = { hidden: true, textContent: '' };
  var popover = { hidden: true };
  global.setTimeout = function (callback) {
    pendingTimers.push(callback);
    return pendingTimers.length;
  };
  global.clearTimeout = function () {};
  try {
    var ctrl = timer.create({
      url: '/ladder/retry/timing/',
      document: { visibilityState: 'visible', addEventListener: function () {} },
      getAnonKey: function () { return ''; },
      getCsrf: function () { return ''; },
      storage: memoryStorage(),
      localStorage: memoryStorage(),
      fetch: function (url, init) {
        starts.push(JSON.parse(init.body));
        return Promise.resolve({
          json: function () {
            return Promise.resolve(fail ? {} : { ok: true, status: 'running', exists: true });
          },
        });
      },
      listenDocument: false,
      enableHeartbeat: false,
      enableBroadcast: false,
      errorEl: errorEl,
      popover: popover,
      bootstrap: { status: 'auto_paused', accumulated_ms: 0, exists: false },
    });
    async function settlePost() {
      await Promise.resolve();
      await Promise.resolve();
    }
    ctrl.startIfAllowed();
    await settlePost();
    while (pendingTimers.length) {
      pendingTimers.shift()();
      await settlePost();
    }
    assert.strictEqual(errorEl.hidden, false);
    assert.match(errorEl.textContent, /не записывается/);
    assert.strictEqual(popover.hidden, false);
    var firstStart = starts[0];
    assert.strictEqual(starts.length, 6);
    starts.forEach(function (event) {
      assert.strictEqual(event.event_id, firstStart.event_id);
      assert.strictEqual(event.seq, firstStart.seq);
      assert.strictEqual(event.action, firstStart.action);
      assert.strictEqual(event.client_occurred_at, firstStart.client_occurred_at);
      assert.strictEqual(event.event_protocol, undefined);
    });

    fail = false;
    ctrl.startIfAllowed();
    await settlePost();
    assert.strictEqual(errorEl.hidden, true);
    assert.strictEqual(starts[starts.length - 1].event_id, firstStart.event_id);
    ctrl.destroy();
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
})().catch(function (error) {
  console.error(error);
  process.exitCode = 1;
});

(async function testTiming503RetriesIdenticalEventAndHonorsRetryAfter() {
  var originalSetTimeout = global.setTimeout;
  var originalRandom = Math.random;
  var timers = [];
  var delays = [];
  var sent = [];
  var calls = 0;
  global.setTimeout = function (callback, delay) {
    timers.push(callback);
    delays.push(delay);
    return timers.length;
  };
  Math.random = function () { return 0.5; };
  try {
    var ctrl = timer.create({
      url: '/ladder/retry/timing/',
      document: { visibilityState: 'visible', addEventListener: function () {} },
      getAnonKey: function () { return ''; },
      getCsrf: function () { return ''; },
      storage: memoryStorage(),
      localStorage: memoryStorage(),
      fetch: function (url, init) {
        sent.push(JSON.parse(init.body));
        calls += 1;
        return Promise.resolve(calls <= 2 ? {
          status: 503,
          headers: { get: function () { return '2'; } },
          json: function () { return Promise.resolve({ ok: false, error: 'temporarily_unavailable' }); },
        } : {
          status: 200,
          headers: { get: function () { return null; } },
          json: function () { return Promise.resolve({ ok: true, status: 'manually_paused', exists: true }); },
        });
      },
      listenDocument: false,
      enableHeartbeat: false,
      enableBroadcast: false,
      bootstrap: { status: 'running', is_authoritative: true, exists: true },
    });
    var resultPromise = ctrl.pauseManual();
    await Promise.resolve();
    await Promise.resolve();
    assert.strictEqual(timers.length, 1);
    assert.strictEqual(delays[0], 2125);
    timers.shift()();
    await Promise.resolve();
    await Promise.resolve();
    assert.strictEqual(timers.length, 1);
    assert.strictEqual(delays[1], 2250);
    timers.shift()();
    await Promise.resolve();
    await Promise.resolve();
    var result = await resultPromise;
    assert.strictEqual(result.ok, true);
    assert.strictEqual(sent.length, 3);
    assert.deepStrictEqual(sent[1], sent[0]);
    assert.deepStrictEqual(sent[2], sent[0]);
    ctrl.destroy();
  } finally {
    global.setTimeout = originalSetTimeout;
    Math.random = originalRandom;
  }
})().catch(function (error) {
  console.error(error);
  process.exitCode = 1;
});

(async function testNewClientRetriesOnlyLegacyBusy409WithoutCapability() {
  var oldSetTimeout = global.setTimeout;
  var oldRandom = Math.random;
  var timers = [];
  var sent = [];
  var calls = 0;
  global.setTimeout = function (callback, delay) {
    timers.push({ callback: callback, delay: delay });
    return timers.length;
  };
  Math.random = function () { return 0.5; };
  try {
    var ctrl = timer.create({
      url: '/ladder/legacy-backend/timing/',
      document: { visibilityState: 'visible', addEventListener: function () {} },
      getAnonKey: function () { return ''; },
      getCsrf: function () { return ''; },
      storage: memoryStorage(),
      localStorage: memoryStorage(),
      fetch: function (url, init) {
        sent.push(JSON.parse(init.body));
        calls += 1;
        return Promise.resolve(calls === 1 ? {
          status: 409,
          headers: { get: function () { return null; } },
          json: function () { return Promise.resolve({ ok: false, error: 'busy' }); },
        } : {
          status: 200,
          headers: { get: function () { return null; } },
          json: function () { return Promise.resolve({ ok: true, status: 'manually_paused', exists: true }); },
        });
      },
      listenDocument: false,
      enableHeartbeat: false,
      enableBroadcast: false,
      bootstrap: { status: 'running', is_authoritative: true, exists: true },
    });
    var pending = ctrl.pauseManual();
    await Promise.resolve();
    await Promise.resolve();
    assert.strictEqual(sent.length, 1);
    assert.strictEqual(timers.length, 1);
    timers.shift().callback();
    var result = await pending;
    assert.strictEqual(result.ok, true);
    assert.strictEqual(sent.length, 2);
    assert.deepStrictEqual(sent[1], sent[0]);
    assert.strictEqual(sent[0].event_protocol, undefined);
    ctrl.destroy();
  } finally {
    global.setTimeout = oldSetTimeout;
    Math.random = oldRandom;
  }
})().catch(function (error) {
  console.error(error);
  process.exitCode = 1;
});

(async function testEventIdConflict409IsNotRetriedOnNewProtocol() {
  var sent = [];
  var ctrl = timer.create({
    url: '/ladder/current-backend/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return ''; },
    getCsrf: function () { return ''; },
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function (url, init) {
      sent.push(JSON.parse(init.body));
      return Promise.resolve({
        status: 409,
        headers: { get: function () { return null; } },
        json: function () { return Promise.resolve({ ok: false, error: 'event_id_conflict' }); },
      });
    },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    bootstrap: { status: 'running', is_authoritative: true, exists: true, timing_event_capability: 'stable-event-v1' },
  });
  var result = await ctrl.pauseManual();
  assert.strictEqual(result.error, 'event_id_conflict');
  assert.strictEqual(sent.length, 1);
  ctrl.destroy();
})().catch(function (error) {
  console.error(error);
  process.exitCode = 1;
});

(async function testTimingQueueSerializesResumePauseAndCompletion() {
  var requests = [];
  var resolvers = [];
  var inFlight = 0;
  var maxInFlight = 0;
  var ctrl = timer.create({
    url: '/ladder/queue/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return ''; },
    getCsrf: function () { return ''; },
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function (url, init) {
      requests.push(JSON.parse(init.body));
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      return new Promise(function (resolve) {
        resolvers.push(function () {
          inFlight -= 1;
          resolve({ status: 200, json: function () {
            return Promise.resolve({ ok: true, status: 'running', exists: true });
          } });
        });
      });
    },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    bootstrap: { status: 'manually_paused', manually_paused: true, exists: true },
  });
  async function settle() { for (var i = 0; i < 12; i += 1) await Promise.resolve(); }
  var resume = ctrl.resumeManual();
  var pause = ctrl.pauseManual();
  ctrl.markComplete(null);
  await settle();
  assert.deepStrictEqual(requests.map(function (x) { return x.action; }), ['resume']);
  resolvers.shift()();
  await settle();
  assert.deepStrictEqual(requests.map(function (x) { return x.action; }), ['resume', 'pause']);
  resolvers.shift()();
  await settle();
  assert.deepStrictEqual(requests.map(function (x) { return x.action; }), ['resume', 'pause', 'complete']);
  resolvers.shift()();
  await Promise.all([resume, pause]);
  await settle();
  assert.strictEqual(maxInFlight, 1);
  assert.deepStrictEqual(requests.map(function (x) { return x.seq; }), [1, 2, 3]);
  ctrl.destroy();
})().catch(function (error) { console.error(error); process.exitCode = 1; });

(async function testOfflinePendingEventResumesWithSameEnvelope() {
  var oldNavigator = Object.getOwnPropertyDescriptor(global, 'navigator');
  var oldAddEventListener = global.addEventListener;
  var onlineHandler = null;
  var sent = [];
  var expiryTimers = [];
  Object.defineProperty(global, 'navigator', { configurable: true, value: { onLine: false } });
  global.addEventListener = function (name, callback) { if (name === 'online') onlineHandler = callback; };
  var oldSetTimeout = global.setTimeout;
  global.setTimeout = function (callback) { expiryTimers.push(callback); return expiryTimers.length; };
  try {
    var ctrl = timer.create({
      url: '/ladder/offline/timing/',
      document: { visibilityState: 'visible', addEventListener: function () {} },
      getAnonKey: function () { return ''; },
      getCsrf: function () { return ''; },
      storage: memoryStorage(),
      localStorage: memoryStorage(),
      fetch: function (url, init) {
        sent.push(JSON.parse(init.body));
        return Promise.resolve({ status: 200, json: function () {
          return Promise.resolve({ ok: true, status: 'manually_paused', exists: true });
        } });
      },
      listenDocument: false,
      enableHeartbeat: false,
      enableBroadcast: false,
      bootstrap: { status: 'running', is_authoritative: true, exists: true },
    });
    var ack = ctrl.pauseManual();
    await Promise.resolve();
    assert.strictEqual(sent.length, 0);
    global.navigator.onLine = true;
    onlineHandler();
    var result = await ack;
    assert.strictEqual(result.ok, true);
    assert.strictEqual(sent.length, 1);
    assert.strictEqual(sent[0].action, 'pause');
    ctrl.destroy();
  } finally {
    global.setTimeout = oldSetTimeout;
    global.addEventListener = oldAddEventListener;
    if (oldNavigator) Object.defineProperty(global, 'navigator', oldNavigator);
    else delete global.navigator;
    void expiryTimers;
  }
})().catch(function (error) { console.error(error); process.exitCode = 1; });

(async function testEventRetryBudgetExhaustionIsFiniteAndVisible() {
  var oldSetTimeout = global.setTimeout;
  var oldClearTimeout = global.clearTimeout;
  var oldRandom = Math.random;
  var timers = [];
  var sent = [];
  var errorEl = { hidden: true, textContent: '' };
  global.setTimeout = function (callback) { timers.push(callback); return timers.length; };
  global.clearTimeout = function () {};
  Math.random = function () { return 0.5; };
  try {
    var ctrl = timer.create({
      url: '/ladder/retry-exhaust/timing/',
      document: { visibilityState: 'visible', addEventListener: function () {} },
      getAnonKey: function () { return ''; },
      getCsrf: function () { return ''; },
      storage: memoryStorage(),
      localStorage: memoryStorage(),
      errorEl: errorEl,
      fetch: function (url, init) {
        sent.push(JSON.parse(init.body));
        return Promise.resolve({
          status: 503,
          headers: { get: function () { return '0'; } },
          json: function () { return Promise.resolve({ ok: false, error: 'temporarily_unavailable' }); },
        });
      },
      listenDocument: false,
      enableHeartbeat: false,
      enableBroadcast: false,
      bootstrap: { status: 'running', is_authoritative: true, exists: true },
    });
    var pending = ctrl.pauseManual();
    for (var i = 0; i < 3; i += 1) {
      for (var j = 0; j < 8; j += 1) await Promise.resolve();
      if (timers.length) timers.shift()();
    }
    var result = await pending;
    assert.strictEqual(result.ok, false);
    assert.strictEqual(sent.length, 3);
    assert.deepStrictEqual(sent[1], sent[0]);
    assert.deepStrictEqual(sent[2], sent[0]);
    assert.strictEqual(errorEl.hidden, false);
    assert.strictEqual(timers.length, 0);
    ctrl.destroy();
  } finally {
    global.setTimeout = oldSetTimeout;
    global.clearTimeout = oldClearTimeout;
    Math.random = oldRandom;
  }
})().catch(function (error) { console.error(error); process.exitCode = 1; });

(function testNewClientOnlyOptsIntoBackendAdvertisedCapability() {
  var bodies = [];
  var responseIndex = 0;
  var ctrl = timer.create({
    url: '/ladder/capability/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return ''; },
    getCsrf: function () { return ''; },
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function (url, init) {
      responseIndex += 1;
      if (init && init.method === 'POST') bodies.push(JSON.parse(init.body));
      var body = responseIndex === 1
        ? { ok: true, status: 'auto_paused', exists: false, timing_event_capability: 'stable-event-v1' }
        : { ok: true, status: 'running', exists: true };
      return Promise.resolve({ status: 200, json: function () { return Promise.resolve(body); } });
    },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    bootstrap: { status: 'auto_paused', exists: false },
  });
  ctrl.boot();
  setTimeout(function () {
    assert.strictEqual(bodies.length, 1);
    assert.strictEqual(bodies[0].event_protocol, 1);
    assert.strictEqual(bodies[0].event_capability, 'stable-event-v1');
    ctrl.destroy();
  }, 10);
})();

(function testPersonalModeDoesNotShowForeignSessionOverlay() {
  var overlay = { hidden: true };
  var board = {
    setAttribute: function () {},
    removeAttribute: function () {},
    closest: function () { return null; },
  };
  var rootEl = {
    hidden: false,
    classList: { toggle: function () {} },
  };
  var ctrl = timer.create({
    url: '/ladder/personal/timing/',
    document: {
      visibilityState: 'visible',
      body: { classList: { toggle: function () {} } },
      addEventListener: function () {},
      dispatchEvent: function () {},
    },
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
    root: rootEl,
    boardEl: board,
    overlay: overlay,
    bootstrap: {
      status: 'running',
      is_authoritative: false,
      accumulated_ms: 1000,
      exists: true,
    },
  });
  assert.strictEqual(overlay.hidden, true);
  assert.strictEqual(ctrl.state().is_authoritative, false);
  ctrl.destroy();
})();

(function testManualPauseShowsOverlayAndMakesBoardInert() {
  var overlay = { hidden: true };
  var boardAttributes = {};
  var board = {
    setAttribute: function (name, value) { boardAttributes[name] = value; },
    removeAttribute: function (name) { delete boardAttributes[name]; },
    closest: function () { return null; },
  };
  var ctrl = timer.create({
    url: '/censorly/1/timing/',
    document: {
      visibilityState: 'visible',
      body: { classList: { toggle: function () {} } },
      addEventListener: function () {},
      dispatchEvent: function () {},
    },
    getAnonKey: function () { return ''; },
    getCsrf: function () { return ''; },
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    boardEl: board,
    overlay: overlay,
    bootstrap: {
      status: 'manually_paused',
      manually_paused: true,
      is_authoritative: false,
      accumulated_ms: 1000,
      exists: true,
    },
  });

  assert.strictEqual(overlay.hidden, false);
  assert.strictEqual(boardAttributes.inert, '');
  ctrl.destroy();
})();

console.log('daily_solve_timer tests ok');
process.exit(0);
