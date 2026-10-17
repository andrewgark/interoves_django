'use strict';

var assert = require('assert');
var reconnect = null;
var reloads = 0;
var periodicSync = null;
var visibilityHandlers = [];
var timeoutDelays = [];
var timeoutCallbacks = [];

function FakeWebSocket(url) {
  this.url = url;
  this.readyState = 1;
  this.sent = [];
  FakeWebSocket.instances.push(this);
}
FakeWebSocket.instances = [];
FakeWebSocket.prototype.send = function (value) { this.sent.push(JSON.parse(value)); };
FakeWebSocket.prototype.close = function () {};

global.WebSocket = FakeWebSocket;
global.setInterval = function (fn) { periodicSync = fn; return 1; };
global.clearInterval = function () {};
global.setTimeout = function (fn, delay) {
  reconnect = fn;
  timeoutDelays.push(delay);
  timeoutCallbacks.push(fn);
  return timeoutCallbacks.length;
};
global.clearTimeout = function () {};
global.window = {
  document: {
    visibilityState: 'visible',
    addEventListener: function (name, handler) {
      if (name === 'visibilitychange') visibilityHandlers.push(handler);
    },
    removeEventListener: function () {},
  },
  location: {
    protocol: 'https:',
    host: 'example.test',
    reload: function () { reloads += 1; },
  },
};

require('./track_ws.js');

var seen = {};
assert.strictEqual(window.InterovesTrack.acceptFreshSequence({seq: 2, seq_namespace: 'n'}, seen), true);
assert.strictEqual(window.InterovesTrack.acceptFreshSequence({seq: 2, seq_namespace: 'n'}, seen), false);
assert.strictEqual(window.InterovesTrack.acceptFreshSequence({seq: 1, seq_namespace: 'n'}, seen), false);
assert.strictEqual(window.InterovesTrack.acceptFreshSequence({seq: 3, seq_namespace: 'n'}, seen), true);

var phaseRoot = {
  getAttribute: function (name) {
    return {
      'data-game-start-at': '2026-08-20T12:00:00Z',
      'data-game-end-at': '2026-08-20T13:00:00Z',
    }[name] || '';
  },
};
assert.strictEqual(
  window.InterovesTrack.nextGamePhaseAt(phaseRoot, Date.parse('2026-08-20T11:00:00Z')),
  Date.parse('2026-08-20T12:00:00Z')
);
assert.strictEqual(
  window.InterovesTrack.nextGamePhaseAt(phaseRoot, Date.parse('2026-08-20T12:30:00Z')),
  Date.parse('2026-08-20T13:00:00Z')
);
assert.strictEqual(
  window.InterovesTrack.nextGamePhaseAt(phaseRoot, Date.parse('2026-08-20T14:00:00Z')),
  null
);
var dailyRoot = {
  getAttribute: function (name) {
    return name === 'data-live-next-transition-at' ? '2026-08-21T00:00:00+03:00' : '';
  },
};
assert.strictEqual(
  window.InterovesTrack.nextGamePhaseAt(dailyRoot, Date.parse('2026-08-20T20:00:00Z')),
  Date.parse('2026-08-21T00:00:00+03:00')
);
assert.strictEqual(window.InterovesTrack.transitionJitterMs(
  dailyRoot, Date.parse('2026-08-21T00:00:00+03:00')
), 20000);
var seed = 17;
var clientDelays = {};
for (var client = 0; client < 500; client += 1) {
  seed = (seed * 48271) % 2147483647;
  var virtualDelay = window.InterovesTrack.randomDelayMs(20000, seed / 2147483647);
  assert(virtualDelay >= 0 && virtualDelay <= 20000);
  clientDelays[virtualDelay] = true;
}
assert(Object.keys(clientDelays).length > 450);
assert.strictEqual(window.InterovesTrack.randomDelayMs(3000, 0.5), 1500);
var priorRandom = Math.random;
Math.random = function () { return 0.5; };
var reloadTimerCount = timeoutCallbacks.length;
window.InterovesTrack.scheduleReload('daily:test', 20000);
window.InterovesTrack.scheduleReload('daily:test', 20000);
assert.strictEqual(timeoutCallbacks.length, reloadTimerCount + 1);
assert.strictEqual(timeoutDelays[timeoutDelays.length - 1], 10000);
Math.random = priorRandom;
assert.deepStrictEqual(window.InterovesTrack.collectTaskIds({
  querySelectorAll: function () {
    return [
      {id: 'new-task-12'},
      {id: 'new-task-hints-12'},
      {id: 'new-task-12'},
      {id: 'new-task-18'},
    ];
  },
}), ['12', '18']);

window.InterovesTrack.openTrackSocket('wss://example.test/games/g/track');
var first = FakeWebSocket.instances[0];
first.onopen();
assert.deepStrictEqual(first.sent[0], { type: 'track.sync', seen: {} });

first.onmessage({data: JSON.stringify({
  type: 'track.synced',
  versions: {'game:g:team:7': 4},
  missed: {},
})});
periodicSync();
assert.deepStrictEqual(first.sent[1], {
  type: 'track.sync',
  seen: {'game:g:team:7': 4},
});
visibilityHandlers[0]();
assert.deepStrictEqual(first.sent[2], {
  type: 'track.sync',
  seen: {'game:g:team:7': 4},
});
first.onclose();
assert.strictEqual(typeof reconnect, 'function');
var reconnectDelay = timeoutDelays[timeoutDelays.length - 1];
assert(reconnectDelay >= 1000 && reconnectDelay <= 6000);
reconnect();

window.InterovesTrack.connectGame('scoped', '/glowbyte/games/scoped/track');
assert.strictEqual(
  FakeWebSocket.instances[2].url,
  'wss://example.test/glowbyte/games/scoped/track'
);

var second = FakeWebSocket.instances[1];
second.onopen();
assert.deepStrictEqual(second.sent[0], {
  type: 'track.sync',
  seen: {'game:g:team:7': 4},
});
second.onmessage({data: JSON.stringify({
  type: 'track.resync_required',
  versions: {'game:g:team:7': 5},
  missed: {'game:g:team:7': 5},
})});
// Recovery failure is jittered instead of reloading in the message callback.
assert.strictEqual(reloads, 0);

var delivered = [];
var finishReconcile = null;
window.InterovesTrack.openTrackSocket(
  'wss://example.test/games/queued/track',
  function (msg) { delivered.push(msg.seq); },
  function () {
    return new Promise(function (resolve) { finishReconcile = resolve; });
  }
);
var queuedSocket = FakeWebSocket.instances[3];
queuedSocket.onopen();
queuedSocket.onmessage({data: JSON.stringify({
  type: 'track.synced',
  versions: {'game:queued:team:7': 5},
})});
queuedSocket.onmessage({data: JSON.stringify({
  type: 'track.resync_required',
  versions: {'game:queued:team:7': 6},
  missed: {'game:queued:team:7': 6},
})});
queuedSocket.onmessage({data: JSON.stringify({
  type: 'task.changed',
  seq_namespace: 'game:queued:team:7',
  seq: 7,
})});
assert.deepStrictEqual(delivered, []);
finishReconcile({versions: {'game:queued:team:7': 6}});
Promise.resolve().then(function () {
  assert.deepStrictEqual(delivered, [7]);
  var scoped = [];
  window.InterovesTrack.openTrackSocket(
    'wss://example.test/games/scoped-filter/track',
    function (msg) { scoped.push(msg.seq); },
    null,
    null,
    function (msg) { return String(msg.game_id) === 'wanted'; }
  );
  var scopedSocket = FakeWebSocket.instances[4];
  scopedSocket.onopen();
  scopedSocket.onmessage({data: JSON.stringify({
    type: 'track.synced',
    versions: {'user:1:game:wanted': 0},
  })});
  scopedSocket.onmessage({data: JSON.stringify({
    type: 'task.changed', game_id: 'other',
    seq_namespace: 'user:1:game:wanted', seq: 1,
  })});
  scopedSocket.onmessage({data: JSON.stringify({
    type: 'task.changed', game_id: 'wanted',
    seq_namespace: 'user:1:game:wanted', seq: 1,
  })});
  assert.deepStrictEqual(scoped, [1]);

  var resetDelivered = [];
  window.InterovesTrack.openTrackSocket(
    'wss://example.test/games/reset/track',
    function (msg) { resetDelivered.push(msg.seq); },
    function (msg) { return Promise.resolve({versions: msg.versions}); }
  );
  var resetSocket = FakeWebSocket.instances[5];
  resetSocket.onopen();
  resetSocket.onmessage({data: JSON.stringify({
    type: 'track.synced', versions: {'game:reset:team:1': 9},
  })});
  resetSocket.onmessage({data: JSON.stringify({
    type: 'track.resync_required',
    versions: {'game:reset:team:1': 2},
    reset: {'game:reset:team:1': 2},
  })});
  return Promise.resolve().then(function () {
    resetSocket.onmessage({data: JSON.stringify({
      type: 'task.changed', seq_namespace: 'game:reset:team:1', seq: 3,
    })});
    assert.deepStrictEqual(resetDelivered, [3]);
    console.log('track_ws.test.js: ok');
  });
});
