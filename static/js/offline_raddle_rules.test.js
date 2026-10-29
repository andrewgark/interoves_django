'use strict';

var assert = require('assert');
var path = require('path');
require('./offline_normalize.js');
require('./offline_raddle_rules.js');
var R = global.OfflineRaddleRules;

// ── Shared contract with games/puzzles/raddle/transition.py ───────────────
var contract = require(path.join(__dirname, '..', '..', 'contracts', 'raddle_rules.json'));
var checked = 0;
contract.ladders.forEach(function (entry) {
  var ladder = entry.ladder;
  entry.cases.forEach(function (c) {
    var label = JSON.stringify(ladder.lengths) + ' ' + JSON.stringify(c);
    assert.deepStrictEqual(R.playableWordIndices(c.state, ladder.n_words), c.playable, label);
    assert.strictEqual(R.refuseWord(c.state, ladder, c.word_index, c.word), c.refusal, label);
    if (c.refusal !== null) return;
    var after = R.advance(c.state, ladder, c.word_index);
    assert.deepStrictEqual(
      { solved_indices: after.solved_indices, used_hints: after.used_hints }, c.advanced, label,
    );
    assert.deepStrictEqual(R.progressStatus(after, ladder.n_words, true), c.status_correct, label);
    assert.deepStrictEqual(R.progressStatus(c.state, ladder.n_words, false), c.status_wrong, label);
    checked++;
  });
});
assert.ok(checked > 20, 'too few accepted moves in the contract: ' + checked);

// ── advance keeps the other state keys and does not mutate its input ──────
var state = { solved_indices: [0, 2], used_hints: [], drafts: { 1: 'аб' } };
var after = R.advance(state, { n_words: 3, n_hints: 2, lengths: [3, 3, 3] }, 1);
assert.deepStrictEqual(state.solved_indices, [0, 2]);
assert.deepStrictEqual(after.drafts, { 1: 'аб' });
assert.deepStrictEqual(after.used_hints, [0, 1]);

console.log('offline_raddle_rules.test.js: ok');
