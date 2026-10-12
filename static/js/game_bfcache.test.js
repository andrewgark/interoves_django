'use strict';

var assert = require('assert');
var listeners = {};
var reloads = 0;
global.window = {
  addEventListener: function (name, callback) { listeners[name] = callback; },
  location: { reload: function () { reloads += 1; } },
};
global.document = {
  body: { getAttribute: function () { return '1'; } },
};

require('./game_bfcache.js');

listeners.pageshow({ persisted: false });
assert.strictEqual(reloads, 0);
listeners.pageshow({ persisted: true });
assert.strictEqual(reloads, 1);
listeners.pageshow({ persisted: true });
assert.strictEqual(reloads, 1);

global.document.body.getAttribute = function () { return null; };
delete require.cache[require.resolve('./game_bfcache.js')];
listeners = {};
require('./game_bfcache.js');
listeners.pageshow({ persisted: true });
assert.strictEqual(reloads, 1);

console.log('game_bfcache.test.js: ok');
