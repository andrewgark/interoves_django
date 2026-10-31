'use strict';

// The same page is served on interoves.com and ru.interoves.com. Track
// sockets must follow the page's own host, never a hard-coded domain.

var assert = require('assert');

function FakeWebSocket(url) {
  this.url = url;
  this.readyState = 0;
  FakeWebSocket.instances.push(this);
}
FakeWebSocket.instances = [];
FakeWebSocket.prototype.send = function () {};
FakeWebSocket.prototype.close = function () {};

global.WebSocket = FakeWebSocket;
global.setInterval = function () { return 1; };
global.clearInterval = function () {};
global.setTimeout = function () { return 1; };
global.clearTimeout = function () {};
global.window = {
  document: {
    visibilityState: 'visible',
    addEventListener: function () {},
    removeEventListener: function () {},
  },
  location: { protocol: 'https:', host: 'ru.interoves.com', reload: function () {} },
};

require('./track_ws.js');

function lastUrl() {
  return FakeWebSocket.instances[FakeWebSocket.instances.length - 1].url;
}

window.InterovesTrack.connectUserHub();
assert.strictEqual(lastUrl(), 'wss://ru.interoves.com/ws/track/');

window.InterovesTrack.connectGame('daily', '/games/daily/track', 'personal');
assert.strictEqual(lastUrl(), 'wss://ru.interoves.com/games/daily/track?play_mode=personal');

window.InterovesTrack.connectGame('scoped', '/glowbyte/games/scoped/track', '');
assert.strictEqual(lastUrl(), 'wss://ru.interoves.com/glowbyte/games/scoped/track');

window.location.host = 'interoves.com';
window.InterovesTrack.connectGame('main', '', '');
assert.strictEqual(lastUrl(), 'wss://interoves.com/games/main/track');

window.location.protocol = 'http:';
window.location.host = 'localhost:8000';
window.InterovesTrack.connectUserHub();
assert.strictEqual(lastUrl(), 'ws://localhost:8000/ws/track/');

console.log('track_ws host tests ok');
