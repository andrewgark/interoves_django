'use strict';

var assert = require('assert');
var path = require('path');
require('./offline_normalize.js');
require('./offline_raddle_rules.js');
require('./offline_raddle_pack.js');
var P = global.OfflineRaddlePack;

// ── Shared contract with games/puzzles/raddle/offline_pack.slot_keys ──────
var contract = require(path.join(__dirname, '..', '..', 'contracts', 'raddle_slot_keys.json'));
assert.ok(contract.cases.length > 20);
contract.cases.forEach(function (c) {
  assert.deepStrictEqual(P.slotKeys(c.state, c.n, c.tiers), c.keys, JSON.stringify(c.state));
});

console.log('offline_raddle_pack.test.js: ok');
