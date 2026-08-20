'use strict';

var assert = require('assert');
var reconnect = null;
var reloads = 0;
var periodicSync = null;
var visibilityHandlers = [];

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
global.setTimeout = function (fn) { reconnect = fn; return 1; };
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
reconnect();

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
assert.strictEqual(reloads, 1);

var delivered = [];
var finishReconcile = null;
window.InterovesTrack.openTrackSocket(
  'wss://example.test/games/queued/track',
  function (msg) { delivered.push(msg.seq); },
  function () {
    return new Promise(function (resolve) { finishReconcile = resolve; });
  }
);
var queuedSocket = FakeWebSocket.instances[2];
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
  console.log('track_ws.test.js: ok');
});
