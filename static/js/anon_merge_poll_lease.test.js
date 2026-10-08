'use strict';

var assert = require('assert');
require('./anon_merge_poll_lease.js');

function makeStorage() {
  var values = {};
  return {
    getItem: function (key) { return values[key] || null; },
    setItem: function (key, value) { values[key] = value; },
    removeItem: function (key) { delete values[key]; },
  };
}

var clock = 1000;
var storage = makeStorage();
var first = global.InterovesAnonMergePollLease.create({
  storage: storage, key: 'merge', owner: 'first', now: function () { return clock; }, leaseMs: 100,
});
var second = global.InterovesAnonMergePollLease.create({
  storage: storage, key: 'merge', owner: 'second', now: function () { return clock; }, leaseMs: 100,
});

assert.strictEqual(first.acquire(), true);
assert.strictEqual(second.acquire(), false);
assert.strictEqual(first.renew(), true);
first.release();
assert.strictEqual(second.acquire(), true);

clock += 101;
assert.strictEqual(first.acquire(), true);
assert.strictEqual(second.acquire(), false);
second.release();
assert.strictEqual(first.acquire(), true);

console.log('anon_merge_poll_lease.test.js: ok');
