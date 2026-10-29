'use strict';

var assert = require('assert');
var path = require('path');
require('./offline_normalize.js');
var N = global.OfflineNormalize;

// ── Shared contract with games/puzzles/core/normalize_ops.py ──────────────
var contract = require(path.join(__dirname, '..', '..', 'contracts', 'normalize.json'));
assert.ok(contract.cases.length > 0);
contract.cases.forEach(function (c) {
  assert.strictEqual(
    N.normalize(c.in, c.ops), c.out,
    JSON.stringify(c.ops) + ' ' + JSON.stringify(c.in),
  );
});

// ── Named pipelines mirror the Python constants ───────────────────────────
assert.deepStrictEqual(N.CLEAN, ['lower', 'strip', 'yo_to_e']);
assert.deepStrictEqual(N.CLEAN_KEEP_YO, ['lower', 'strip']);
assert.deepStrictEqual(N.RADDLE_WORD, ['keep_word_chars', 'lower', 'strip', 'yo_to_e']);

// ── Unknown steps fail loudly instead of being skipped ────────────────────
assert.throws(function () { N.normalize('x', ['lower', 'nope']); }, /nope/);

// ── null / undefined behave like an empty answer ─────────────────────────
assert.strictEqual(N.normalize(null, N.CLEAN), '');
assert.strictEqual(N.normalize(undefined, N.CLEAN), '');

console.log('offline_normalize.test.js: ok');
