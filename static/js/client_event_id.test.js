'use strict';

var assert = require('assert');
require('./client_event_id.js');
var ids = global.InterovesClientEventId;

var V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

// ── crypto.randomUUID ─────────────────────────────────────────────────────
assert.ok(V4.test(ids.create()));
assert.notStrictEqual(ids.create(), ids.create());

// ── Fallback without crypto still yields a v4 UUID ────────────────────────
var savedCrypto = Object.getOwnPropertyDescriptor(global, 'crypto');
Object.defineProperty(global, 'crypto', { value: undefined, configurable: true });
try {
  var fallback = ids.create();
  assert.ok(V4.test(fallback), fallback);
} finally {
  Object.defineProperty(global, 'crypto', savedCrypto);
}

assert.ok(ids.isUuid('123e4567-e89b-42d3-a456-426614174000'));
assert.ok(!ids.isUuid('1729000000000-123456789'));
assert.ok(!ids.isUuid(null));

console.log('client_event_id.test.js: ok');
