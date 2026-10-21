'use strict';

var assert = require('assert');
var timer = require('./daily_solve_timer.js');

// Async tests stub globals such as setTimeout and navigator, so they must run
// one at a time, after the synchronous ones, before the process exits.
var realSetTimeout = setTimeout;
var asyncTests = [];
function asyncTest(fn) { asyncTests.push(fn); }
// Let queued fetch/json promises settle. setImmediate is never stubbed.
function flush() {
  var chain = Promise.resolve();
  for (var i = 0; i < 5; i += 1) chain = chain.then(function () { return new Promise(function (r) { setImmediate(r); }); });
  return chain;
}

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
  var sessionId = store.getItem('session:/ladder/1/timing/');
  assert.ok(sessionId);
  assert.strictEqual(store.getItem('session-seq:' + sessionId), '1');
  second.destroy();
})();

(function testSeqIsPerSessionAcrossUrlsAndMigratesLegacyKey() {
  // One task group is reachable by number and by share hash. The server
  // checks seq per session, so the counter must not restart per URL.
  var store = memoryStorage();
  var tab = { name: '' };
  function make(url) {
    return timer.create({
      url: url,
      document: { visibilityState: 'visible', addEventListener: function () {} },
      windowIdentity: tab,
      storage: store,
      localStorage: memoryStorage(),
      fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({ ok: true }); } }); },
      listenDocument: false,
      enableHeartbeat: false,
      enableBroadcast: false,
      offline: true,
      bootstrap: { status: 'running', is_authoritative: true, exists: true },
    });
  }
  var byNumber = make('/censorly/7/timing/');
  byNumber.onHidden();
  byNumber.onHidden();
  byNumber.destroy();
  var sessionId = byNumber.state().session_id;
  var afterFirst = Number(store.getItem('session-seq:' + sessionId));
  assert.ok(afterFirst >= 1);
  var byHash = make('/censorly/abc123/timing/');
  assert.strictEqual(byHash.state().session_id, sessionId);
  byHash.pauseManual();
  assert.ok(Number(store.getItem('session-seq:' + sessionId)) > afterFirst);
  byHash.destroy();

  // A tab opened before this change still has its counter under the URL key.
  var legacyStore = memoryStorage();
  var legacyTab = { name: 'interoves-daily-timing:11111111-2222-4333-8444-555555555555' };
  legacyStore.setItem('session:/censorly/8/timing/:seq', '41');
  var legacy = timer.create({
    url: '/censorly/8/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    windowIdentity: legacyTab,
    storage: legacyStore,
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({ ok: true }); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'running', is_authoritative: true, exists: true },
  });
  legacy.pauseManual();
  assert.strictEqual(
    legacyStore.getItem('session-seq:11111111-2222-4333-8444-555555555555'),
    '42'
  );
  legacy.destroy();
})();

(function testMultiTaskGroupWithFirstTaskSolvedStillRunsTimer() {
  function card(solved) {
    return { getAttribute: function () { return solved ? '1' : '0'; } };
  }
  var cards = [card(true), card(false)];
  var doc = {
    visibilityState: 'visible',
    addEventListener: function () {},
    getElementById: function (id) {
      return id === 'daily-timing-bootstrap'
        ? { textContent: '{"status":"auto_paused","exists":true}' }
        : null;
    },
    querySelector: function () { return null; },
    querySelectorAll: function () { return cards; },
  };
  var ctrl = timer.bindFromDocument(doc, { url: '' });
  assert.strictEqual(ctrl.state().completed, false);
  ctrl.destroy();
  cards = [card(true), card(true)];
  var done = timer.bindFromDocument(doc, { url: '' });
  assert.strictEqual(done.state().completed, true);
  done.destroy();
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

(function testSessionIdIsUuidAndLegacyNonUuidIdsAreReplaced() {
  // Browsers without crypto.randomUUID used to get "<ms>-<digits>", which the
  // server never accepts as a timer lease (Censorly then rejects every guess).
  var uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  var legacy = '1729000000000-123456789';
  function make(windowIdentity, storage) {
    return timer.create({
      url: '/censorly/1/timing/',
      document: { visibilityState: 'visible', addEventListener: function () {} },
      windowIdentity: windowIdentity,
      storage: storage,
      localStorage: memoryStorage(),
      fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({}); } }); },
      listenDocument: false,
      enableHeartbeat: false,
      enableBroadcast: false,
      offline: true,
      bootstrap: { status: 'auto_paused', exists: false },
    });
  }

  var fresh = make({ name: '' }, memoryStorage());
  assert.ok(uuidRe.test(fresh.state().session_id), fresh.state().session_id);
  fresh.destroy();

  var namedWindow = { name: 'interoves-daily-timing:' + legacy };
  var named = make(namedWindow, memoryStorage());
  assert.ok(uuidRe.test(named.state().session_id));
  assert.strictEqual(namedWindow.name, 'interoves-daily-timing:' + named.state().session_id);
  named.destroy();

  var store = memoryStorage();
  store.setItem('session:/censorly/1/timing/', legacy);
  var stored = make({ name: 'unrelated-window-name' }, store);
  assert.ok(uuidRe.test(stored.state().session_id));
  assert.strictEqual(store.getItem('session:/censorly/1/timing/'), stored.state().session_id);
  stored.destroy();
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

(function testResumeRequiredOverridesStaleRunningAuthority() {
  var clock = fakeClock(0);
  var ctrl = timer.create({
    url: '/censorly/1/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return 'anon'; },
    getCsrf: function () { return ''; },
    clock: clock,
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({}); } }); },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    bootstrap: { status: 'running', is_authoritative: true, accumulated_ms: 1000, exists: true },
  });

  clock.advance(5000);
  ctrl.applySnapshot({
    status: 'running',
    is_authoritative: true,
    resume_required: true,
    accumulated_ms: 5000,
    committed_ms: 1000,
    exists: true,
  }, { replace: true });

  assert.strictEqual(ctrl.state().resume_required, true);
  assert.strictEqual(ctrl.state().is_authoritative, false);
  var pausedAt = ctrl.displayedMs();
  clock.advance(5000);
  assert.strictEqual(ctrl.displayedMs(), pausedAt);
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

asyncTest(async function testFailedResumeRestoresManualPause() {
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
});

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

asyncTest(async function testStartFailureIsVisibleAfterRetriesAndClearsAfterRecovery() {
  var originalSetTimeout = global.setTimeout;
  var originalClearTimeout = global.clearTimeout;
  var pendingTimers = [];
  var fail = true;
  var starts = [];
  var errorEl = { hidden: true, textContent: '' };
  var popover = { hidden: true };
  var recoveryTimers = [];
  global.setTimeout = function (callback, delay) {
    // Start retries are short; the 15 s background recovery is checked
    // separately below.
    (delay >= timer.HEARTBEAT_MS ? recoveryTimers : pendingTimers).push(callback);
    return pendingTimers.length + recoveryTimers.length;
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
      for (var i = 0; i < 5; i += 1) await new Promise(function (r) { setImmediate(r); });
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
    assert.strictEqual(recoveryTimers.length, 1);
    var firstStart = starts[0];
    assert.strictEqual(starts.length, 6);
    starts.forEach(function (event) {
      assert.strictEqual(event.event_id, firstStart.event_id);
      assert.strictEqual(event.seq, firstStart.seq);
      assert.strictEqual(event.action, firstStart.action);
      assert.strictEqual(event.client_occurred_at, firstStart.client_occurred_at);
      assert.strictEqual(event.event_protocol, undefined);
    });

    // The background recovery starts a fresh attempt once the server is back.
    fail = false;
    var before = starts.length;
    recoveryTimers.shift()();
    await settlePost();
    assert.strictEqual(starts.length, before + 1);
    assert.strictEqual(starts[starts.length - 1].action, 'start');
    assert.strictEqual(errorEl.hidden, true);
    ctrl.destroy();
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
});

asyncTest(async function testTiming503RetriesIdenticalEventAndHonorsRetryAfter() {
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
    await flush();
    assert.strictEqual(timers.length, 1);
    assert.strictEqual(delays[0], 2125);
    timers.shift()();
    await flush();
    assert.strictEqual(timers.length, 1);
    assert.strictEqual(delays[1], 2250);
    timers.shift()();
    await flush();
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
});

asyncTest(async function testNewClientRetriesOnlyLegacyBusy409WithoutCapability() {
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
    await flush();
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
});

asyncTest(async function testEventIdConflict409IsNotRetriedOnNewProtocol() {
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
});

asyncTest(async function testTimingQueueSerializesResumePauseAndCompletion() {
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
});

asyncTest(async function testOfflinePendingEventResumesWithSameEnvelope() {
  var oldNavigator = Object.getOwnPropertyDescriptor(global, 'navigator');
  var oldAddEventListener = global.addEventListener;
  var onlineHandlers = [];
  var sent = [];
  var expiryTimers = [];
  Object.defineProperty(global, 'navigator', { configurable: true, value: { onLine: false } });
  global.addEventListener = function (name, callback) { if (name === 'online') onlineHandlers.push(callback); };
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
        if (!global.navigator.onLine) return Promise.reject(new TypeError('Failed to fetch'));
        return Promise.resolve({ status: 200, json: function () {
          return Promise.resolve({ ok: true, status: 'manually_paused', exists: true });
        } });
      },
      listenDocument: false,
      enableHeartbeat: false,
      enableBroadcast: false,
      bootstrap: { status: 'running', is_authoritative: true, exists: true },
    });
    // Chrome can report onLine === false on a working network. That flag
    // alone must not hold back the start, or Censorly rejects every guess.
    var startSent = [];
    var stuck = timer.create({
      url: '/censorly/7/timing/',
      document: { visibilityState: 'visible', addEventListener: function () {} },
      getAnonKey: function () { return ''; },
      getCsrf: function () { return ''; },
      storage: memoryStorage(),
      localStorage: memoryStorage(),
      fetch: function (url, init) {
        startSent.push(JSON.parse(init.body));
        return Promise.resolve({ status: 200, json: function () {
          return Promise.resolve({ ok: true, status: 'running', is_authoritative: true, exists: true });
        } });
      },
      listenDocument: false,
      enableHeartbeat: false,
      enableBroadcast: false,
      bootstrap: { status: 'auto_paused', exists: false },
    });
    stuck.startIfAllowed();
    for (var s = 0; s < 20 && stuck.state().status !== 'running'; s++) await Promise.resolve();
    assert.strictEqual(startSent.length, 1);
    assert.strictEqual(startSent[0].action, 'start');
    assert.strictEqual(stuck.state().is_authoritative, true);
    stuck.destroy();

    var ack = ctrl.pauseManual();
    for (var i = 0; i < 20; i++) await Promise.resolve();
    // One real attempt failed; the queue now waits for connectivity.
    assert.strictEqual(sent.length, 1);
    global.navigator.onLine = true;
    onlineHandlers.forEach(function (handler) { handler(); });
    var result = await ack;
    assert.strictEqual(result.ok, true);
    assert.strictEqual(sent.length, 2);
    assert.strictEqual(sent[1].action, 'pause');
    assert.strictEqual(sent[1].event_id, sent[0].event_id);
    ctrl.destroy();
  } finally {
    global.setTimeout = oldSetTimeout;
    if (oldAddEventListener) global.addEventListener = oldAddEventListener;
    else delete global.addEventListener;
    if (oldNavigator) Object.defineProperty(global, 'navigator', oldNavigator);
    else delete global.navigator;
    void expiryTimers;
  }
});

asyncTest(async function testQuickTabSwitchRestartsAfterAutoPauseDrains() {
  // Back on the tab while auto_pause is still in flight: the start used to be
  // dropped because the queue was busy, leaving the clock stopped.
  var doc = { visibilityState: 'visible', addEventListener: function () {} };
  var sent = [];
  var release = [];
  var ctrl = timer.create({
    url: '/censorly/7/timing/',
    document: doc,
    getAnonKey: function () { return ''; },
    getCsrf: function () { return ''; },
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function (url, init) {
      var action = init.body.charAt(0) === '{'
        ? JSON.parse(init.body).action
        : new URLSearchParams(init.body).get('action');
      sent.push(action);
      var running = action === 'start';
      return new Promise(function (resolve) {
        release.push(function () {
          resolve({ status: 200, json: function () {
            return Promise.resolve({ ok: true, exists: true, status: running ? 'running' : 'auto_paused', is_authoritative: running });
          } });
        });
      });
    },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    bootstrap: { status: 'running', is_authoritative: true, exists: true },
  });
  doc.visibilityState = 'hidden';
  ctrl.onHidden();
  doc.visibilityState = 'visible';
  ctrl.onVisible();
  for (var i = 0; i < 10; i++) await new Promise(function (r) { setImmediate(r); });
  assert.deepStrictEqual(sent, ['auto_pause']);
  release.shift()();
  for (var j = 0; j < 20 && sent.length < 2; j++) await new Promise(function (r) { setImmediate(r); });
  assert.deepStrictEqual(sent, ['auto_pause', 'start']);
  release.shift()();
  for (var k = 0; k < 20 && ctrl.state().status !== 'running'; k++) await new Promise(function (r) { setImmediate(r); });
  assert.strictEqual(ctrl.state().is_authoritative, true);
  ctrl.destroy();
});

asyncTest(async function testHiddenWhileStartInFlightReleasesTheLease() {
  var doc = { visibilityState: 'visible', addEventListener: function () {} };
  var sent = [];
  var release = [];
  var ctrl = timer.create({
    url: '/censorly/7/timing/',
    document: doc,
    getAnonKey: function () { return ''; },
    getCsrf: function () { return ''; },
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function (url, init) {
      var action = init.body.charAt(0) === '{'
        ? JSON.parse(init.body).action
        : new URLSearchParams(init.body).get('action');
      sent.push(action);
      return new Promise(function (resolve) {
        release.push(function () {
          resolve({ status: 200, json: function () {
            return Promise.resolve({
              ok: true, exists: true,
              status: action === 'start' ? 'running' : 'auto_paused',
              is_authoritative: action === 'start',
            });
          } });
        });
      });
    },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    bootstrap: { status: 'auto_paused', exists: false },
  });
  ctrl.startIfAllowed();
  await flush();
  doc.visibilityState = 'hidden';
  ctrl.onHidden();
  assert.deepStrictEqual(sent, ['start']);
  release.shift()();
  await flush();
  // The server opened a lease for a now-hidden tab: close it right away.
  assert.deepStrictEqual(sent, ['start', 'auto_pause']);
  assert.strictEqual(ctrl.state().is_authoritative, false);
  release.shift()();
  await flush();
  ctrl.destroy();
});

asyncTest(async function testBootIgnoresErrorResponseAsSnapshot() {
  var oldRaf = global.requestAnimationFrame;
  var sent = [];
  var ctrl = timer.create({
    url: '/censorly/7/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return ''; },
    getCsrf: function () { return ''; },
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function (url, init) {
      if (!init.method) {
        return Promise.resolve({ ok: false, status: 403, json: function () {
          return Promise.resolve({ ok: false, error: 'club_required' });
        } });
      }
      sent.push(JSON.parse(init.body).action);
      return new Promise(function () {});
    },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    bootstrap: { status: 'auto_paused', exists: false },
  });
  ctrl.boot();
  await flush();
  assert.deepStrictEqual(sent, ['start']);
  assert.strictEqual(ctrl.state().exists, false);
  ctrl.destroy();
  assert.strictEqual(global.requestAnimationFrame, oldRaf);
});

asyncTest(async function testIdleVisibleTabPausesAndActivityRestarts() {
  var oldSetInterval = global.setInterval;
  var oldClearInterval = global.clearInterval;
  var oldNow = Date.now;
  var now = 1000000;
  var tick = null;
  var sent = [];
  Date.now = function () { return now; };
  global.setInterval = function (callback) { tick = callback; return 1; };
  global.clearInterval = function () {};
  try {
    var ctrl = timer.create({
      url: '/censorly/7/timing/',
      document: { visibilityState: 'visible', addEventListener: function () {} },
      getAnonKey: function () { return ''; },
      getCsrf: function () { return ''; },
      storage: memoryStorage(),
      idleMs: 60000,
      fetch: function (url, init) {
        var action = JSON.parse(init.body).action;
        sent.push(action);
        var running = action !== 'auto_pause';
        return Promise.resolve({ status: 200, json: function () {
          return Promise.resolve({ ok: true, exists: true, status: running ? 'running' : 'auto_paused', is_authoritative: running });
        } });
      },
      listenDocument: false,
      enableBroadcast: false,
      bootstrap: { status: 'running', is_authoritative: true, exists: true },
    });
    now += 30000;
    tick();
    await flush();
    assert.deepStrictEqual(sent, ['heartbeat']);

    now += 61000;
    tick();
    await flush();
    assert.deepStrictEqual(sent, ['heartbeat', 'auto_pause']);
    assert.strictEqual(ctrl.state().status, 'auto_paused');
    // Idle never locks the board.
    assert.strictEqual(ctrl.state().resume_required, false);
    // Background recovery must not restart an idle tab.
    ctrl.startIfAllowed();
    await flush();
    assert.deepStrictEqual(sent, ['heartbeat', 'auto_pause']);

    now += 5000;
    ctrl.noteActivity();
    await flush();
    assert.deepStrictEqual(sent, ['heartbeat', 'auto_pause', 'start']);
    assert.strictEqual(ctrl.state().status, 'running');
    ctrl.destroy();
  } finally {
    Date.now = oldNow;
    global.setInterval = oldSetInterval;
    global.clearInterval = oldClearInterval;
  }
});

function reportingTimer(fetchImpl, extra) {
  var options = {
    url: '/censorly/7/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return ''; },
    getCsrf: function () { return 'csrf'; },
    storage: memoryStorage(),
    fetch: fetchImpl,
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    reportProblems: true,
    bootstrap: { status: 'auto_paused', exists: false },
  };
  Object.keys(extra || {}).forEach(function (key) { options[key] = extra[key]; });
  return timer.create(options);
}

function bodyOf(init) {
  return init && init.body && init.body.charAt(0) === '{' ? JSON.parse(init.body) : {};
}

asyncTest(async function testStartFailureIsReportedOnceWithDiagnostics() {
  var oldSetTimeout = global.setTimeout;
  var retries = [];
  var reports = [];
  global.setTimeout = function (callback, delay) {
    if (delay < timer.HEARTBEAT_MS) retries.push(callback);
    return 1;
  };
  try {
    var ctrl = reportingTimer(function (url, init) {
      var body = bodyOf(init);
      if (body.action === 'client_report') {
        reports.push({ body: body, init: init });
        return Promise.resolve({ status: 200, json: function () { return Promise.resolve({ ok: true }); } });
      }
      return Promise.resolve({ status: 403, json: function () {
        return Promise.resolve({ ok: false, error: 'club_required' });
      } });
    });
    ctrl.startIfAllowed();
    await flush();
    while (retries.length) {
      retries.shift()();
      await flush();
    }
    assert.strictEqual(reports.length, 1);
    var report = reports[0].body;
    assert.strictEqual(report.reason, 'start_failed');
    assert.strictEqual(report.details.last_status, 403);
    assert.strictEqual(report.details.last_error, 'club_required');
    assert.strictEqual(report.details.start_attempts, 6);
    assert.strictEqual(reports[0].init.keepalive, true);
    assert.strictEqual(reports[0].init.headers['X-CSRFToken'], 'csrf');
    ctrl.destroy();
  } finally {
    global.setTimeout = oldSetTimeout;
  }
});

asyncTest(async function testAcceptedButStoppedStartIsReported() {
  var reports = [];
  var ctrl = reportingTimer(function (url, init) {
    var body = bodyOf(init);
    if (body.action === 'client_report') {
      reports.push(body);
      return Promise.resolve({ status: 200, json: function () { return Promise.resolve({ ok: true }); } });
    }
    // E.g. the server ignored a start whose seq it considered stale.
    return Promise.resolve({ status: 200, json: function () {
      return Promise.resolve({ ok: true, exists: true, status: 'auto_paused', is_authoritative: false });
    } });
  });
  ctrl.startIfAllowed();
  await flush();
  assert.strictEqual(reports.length, 1);
  assert.strictEqual(reports[0].reason, 'start_not_running');
  assert.strictEqual(reports[0].details.response_status, 'auto_paused');
  ctrl.destroy();
});

asyncTest(async function testWatchdogReportsVisiblePageWhoseClockNeverRan() {
  var oldSetTimeout = global.setTimeout;
  var oldNow = Date.now;
  var now = 5000000;
  var watchdogs = [];
  var reports = [];
  Date.now = function () { return now; };
  global.setTimeout = function (callback, delay) {
    if (delay >= 30000) watchdogs.push(callback);
    return 1;
  };
  try {
    var ctrl = reportingTimer(function (url, init) {
      var body = bodyOf(init);
      if (body.action === 'client_report') {
        reports.push(body);
        return Promise.resolve({ status: 200, json: function () { return Promise.resolve({ ok: true }); } });
      }
      // GET and start never answer: the clock cannot run.
      return new Promise(function () {});
    }, { listenDocument: false });
    ctrl.boot();
    assert.strictEqual(watchdogs.length, 1);
    now += 31000;
    watchdogs.shift()();
    await flush();
    assert.strictEqual(reports.length, 1);
    assert.strictEqual(reports[0].reason, 'never_started');
    ctrl.destroy();
  } finally {
    global.setTimeout = oldSetTimeout;
    Date.now = oldNow;
  }
});

asyncTest(async function testHealthyTimerSendsNoReports() {
  var oldSetTimeout = global.setTimeout;
  var oldNow = Date.now;
  var now = 7000000;
  var watchdogs = [];
  var reports = [];
  Date.now = function () { return now; };
  global.setTimeout = function (callback, delay) {
    if (delay >= 30000) watchdogs.push(callback);
    return 1;
  };
  try {
    var ctrl = reportingTimer(function (url, init) {
      var body = bodyOf(init);
      if (body.action === 'client_report') reports.push(body);
      return Promise.resolve({ status: 200, json: function () {
        return Promise.resolve({ ok: true, exists: true, status: 'running', is_authoritative: true });
      } });
    });
    ctrl.boot();
    await flush();
    ctrl.startIfAllowed();
    await flush();
    now += 31000;
    while (watchdogs.length) watchdogs.shift()();
    await flush();
    assert.strictEqual(ctrl.state().status, 'running');
    assert.deepStrictEqual(reports, []);
    ctrl.destroy();
  } finally {
    global.setTimeout = oldSetTimeout;
    Date.now = oldNow;
  }
});

asyncTest(async function testActivityHealsAStoppedClockAndReportsIt() {
  // The timer ran, then stopped for an unknown reason (e.g. an auto_pause
  // lost while the page was unloading) while the player kept solving.
  var oldNow = Date.now;
  var now = 9000000;
  var actions = [];
  var reports = [];
  Date.now = function () { return now; };
  try {
    var ctrl = reportingTimer(function (url, init) {
      var body = bodyOf(init);
      if (body.action === 'client_report') {
        reports.push(body);
        return Promise.resolve({ status: 200, json: function () { return Promise.resolve({ ok: true }); } });
      }
      actions.push(body.action);
      return Promise.resolve({ status: 200, json: function () {
        return Promise.resolve({ ok: true, exists: true, status: 'running', is_authoritative: true });
      } });
    }, { bootstrap: { status: 'running', is_authoritative: true, exists: true } });
    // Stop the clock locally the way a lost lease does, with no restart.
    ctrl.applySnapshot({ status: 'auto_paused', is_authoritative: false, exists: true });
    assert.strictEqual(ctrl.state().status, 'auto_paused');

    ctrl.noteActivity();
    now += 5000;
    ctrl.noteActivity();
    await flush();
    assert.deepStrictEqual(actions, [], 'no restart before the grace period');

    now += 6000;
    ctrl.noteActivity();
    await flush();
    assert.deepStrictEqual(actions, ['start']);
    assert.strictEqual(ctrl.state().status, 'running');
    assert.deepStrictEqual(reports, [], 'a short stop is healed silently');

    // Stopped again for over a minute of activity: heal and report once.
    ctrl.applySnapshot({ status: 'auto_paused', is_authoritative: false, exists: true });
    ctrl.noteActivity();
    now += 61000;
    ctrl.noteActivity();
    await flush();
    assert.deepStrictEqual(actions, ['start', 'start']);
    assert.strictEqual(reports.length, 1);
    assert.strictEqual(reports[0].reason, 'stopped_while_active');

    // A deliberate pause is left alone.
    ctrl.pauseManual();
    await flush();
    now += 61000;
    ctrl.noteActivity();
    now += 61000;
    ctrl.noteActivity();
    await flush();
    assert.deepStrictEqual(actions, ['start', 'start', 'pause']);
    ctrl.destroy();
  } finally {
    Date.now = oldNow;
  }
});

asyncTest(async function testTimerFailuresNeverLockTheBoard() {
  // Expired lease reported by a heartbeat: restart quietly, no overlay.
  var oldSetInterval = global.setInterval;
  var oldClearInterval = global.clearInterval;
  var heartbeatTick = null;
  global.setInterval = function (callback) { heartbeatTick = callback; return 1; };
  global.clearInterval = function () {};
  var sent = [];
  var ctrl;
  try {
    ctrl = timer.create({
      url: '/censorly/7/timing/',
      document: { visibilityState: 'visible', addEventListener: function () {} },
      getAnonKey: function () { return ''; },
      getCsrf: function () { return ''; },
      storage: memoryStorage(),
      localStorage: memoryStorage(),
      fetch: function (url, init) {
        var body = JSON.parse(init.body);
        sent.push(body);
        var running = body.action !== 'heartbeat';
        return Promise.resolve({ status: 200, json: function () {
          return Promise.resolve({
            ok: true, exists: true, status: running ? 'running' : 'auto_paused',
            is_authoritative: running, manually_paused: false,
          });
        } });
      },
      listenDocument: false,
      enableBroadcast: false,
      bootstrap: { status: 'running', is_authoritative: true, exists: true },
    });
  } finally {
    global.setInterval = oldSetInterval;
    global.clearInterval = oldClearInterval;
  }
  heartbeatTick();
  // The restart is scheduled with setTimeout(0): wait in real time.
  for (var i = 0; i < 100 && sent.length < 2; i++) await new Promise(function (r) { realSetTimeout(r, 5); });
  assert.strictEqual(sent[0].action, 'heartbeat');
  assert.strictEqual(ctrl.state().resume_required, false);
  assert.strictEqual(sent[1] && sent[1].action, 'start');
  for (var j = 0; j < 10 && ctrl.state().status !== 'running'; j++) await Promise.resolve();
  assert.strictEqual(ctrl.state().is_authoritative, true);
  ctrl.destroy();

  // A failed resume unlocks the board instead of re-showing the overlay.
  var resumeCtrl = timer.create({
    url: '/censorly/7/timing/',
    document: { visibilityState: 'visible', addEventListener: function () {} },
    getAnonKey: function () { return ''; },
    getCsrf: function () { return ''; },
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    fetch: function () {
      return Promise.resolve({ status: 403, json: function () {
        return Promise.resolve({ ok: false, error: 'forbidden' });
      } });
    },
    listenDocument: false,
    enableHeartbeat: false,
    enableBroadcast: false,
    bootstrap: { status: 'auto_paused', exists: true, resume_required: true },
  });
  assert.strictEqual(resumeCtrl.state().resume_required, true);
  await resumeCtrl.resumeManual();
  assert.strictEqual(resumeCtrl.state().resume_required, false);
  assert.strictEqual(resumeCtrl.state().manually_paused, false);
  resumeCtrl.destroy();
});

asyncTest(async function testEventRetryBudgetExhaustionIsFiniteAndVisible() {
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
});

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

(function testServerRequiredResumeShowsOverlayAndExposesSessionId() {
  var overlay = { hidden: true };
  var boardAttributes = {};
  var timerAttributes = {};
  var board = {
    setAttribute: function (name, value) { boardAttributes[name] = value; },
    removeAttribute: function (name) { delete boardAttributes[name]; },
    closest: function () { return null; },
  };
  var rootEl = {
    hidden: false,
    classList: { toggle: function () {} },
    setAttribute: function (name, value) { timerAttributes[name] = value; },
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
    root: rootEl,
    boardEl: board,
    overlay: overlay,
    bootstrap: {
      status: 'auto_paused',
      is_authoritative: false,
      accumulated_ms: 1000,
      exists: true,
    },
  });

  ctrl.applySnapshot({
    status: 'auto_paused',
    is_authoritative: false,
    accumulated_ms: 1000,
    exists: true,
    resume_required: true,
  });
  assert.strictEqual(overlay.hidden, false);
  assert.strictEqual(boardAttributes.inert, '');
  assert.strictEqual(timerAttributes['data-timing-session-id'], ctrl.state().session_id);
  ctrl.destroy();
})();

(function testStaleCompletionClearsPauseOverlay() {
  var listeners = {};
  var overlay = { hidden: true };
  var boardAttributes = {};
  var board = {
    setAttribute: function (name, value) { boardAttributes[name] = value; },
    removeAttribute: function (name) { delete boardAttributes[name]; },
    closest: function () { return null; },
  };
  var rootEl = {
    hidden: false,
    classList: { toggle: function () {} },
    setAttribute: function () {},
  };
  var ctrl = timer.create({
    url: '/censorly/1/timing/',
    document: {
      visibilityState: 'visible',
      body: { classList: { toggle: function () {} } },
      addEventListener: function (name, callback) { listeners[name] = callback; },
      dispatchEvent: function () {},
    },
    getAnonKey: function () { return ''; },
    getCsrf: function () { return ''; },
    storage: memoryStorage(),
    localStorage: memoryStorage(),
    enableHeartbeat: false,
    enableBroadcast: false,
    offline: true,
    root: rootEl,
    boardEl: board,
    overlay: overlay,
    bootstrap: { status: 'auto_paused', exists: true },
  });

  ctrl.applySnapshot({
    status: 'auto_paused',
    is_authoritative: false,
    exists: true,
    resume_required: true,
  });
  assert.strictEqual(overlay.hidden, false);
  assert.strictEqual(boardAttributes.inert, '');

  // A visibility/pause update advanced the local revision after this winning
  // gameplay request started. Its completed snapshot must still be terminal.
  listeners['interoves:daily-timing']({ detail: {
    completed: true,
    status: 'completed',
    exists: true,
    accumulated_ms: 5000,
    frozen_ms: 5000,
    _client_revision: 0,
  } });

  assert.strictEqual(ctrl.state().completed, true);
  assert.strictEqual(rootEl.hidden, true);
  assert.strictEqual(overlay.hidden, true);
  assert.strictEqual(Object.prototype.hasOwnProperty.call(boardAttributes, 'inert'), false);
  ctrl.destroy();
})();

var GUARDED_GLOBALS = ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'addEventListener', 'navigator'];

function snapshotGlobals() {
  var snap = {};
  GUARDED_GLOBALS.forEach(function (name) {
    snap[name] = Object.getOwnPropertyDescriptor(global, name);
  });
  snap.random = Math.random;
  return snap;
}

// A test that forgets to restore a stub would silently break the next one.
function restoreGlobals(snap, testName) {
  var leaked = [];
  GUARDED_GLOBALS.forEach(function (name) {
    var now = Object.getOwnPropertyDescriptor(global, name);
    var before = snap[name];
    var same = before && now ? (
      before.value === now.value && before.get === now.get
    ) : before === now;
    if (same) return;
    leaked.push(name);
    if (before) Object.defineProperty(global, name, before);
    else delete global[name];
  });
  if (Math.random !== snap.random) {
    leaked.push('Math.random');
    Math.random = snap.random;
  }
  if (leaked.length) throw new Error(testName + ' leaked stubbed globals: ' + leaked.join(', '));
}

(async function runAsyncTests() {
  var failed = 0;
  for (var i = 0; i < asyncTests.length; i += 1) {
    var fn = asyncTests[i];
    if (process.env.TIMER_TEST && fn.name !== process.env.TIMER_TEST) continue;
    var timeout;
    var globals = snapshotGlobals();
    try {
      await Promise.race([
        fn(),
        new Promise(function (resolve, reject) {
          timeout = realSetTimeout(function () { reject(new Error('timed out')); }, 5000);
        }),
      ]);
    } catch (error) {
      failed += 1;
      console.error('FAIL ' + fn.name + ':', error);
    } finally {
      clearTimeout(timeout);
    }
    try {
      restoreGlobals(globals, fn.name);
    } catch (leak) {
      failed += 1;
      console.error('FAIL ' + leak.message);
    }
  }
  if (failed || process.exitCode) {
    console.error('daily_solve_timer tests FAILED (' + failed + ' async)');
    process.exit(1);
  }
  console.log('daily_solve_timer tests ok');
  process.exit(0);
})();
