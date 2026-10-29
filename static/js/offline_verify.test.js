'use strict';

var assert = require('assert');
var path = require('path');
require('./offline_normalize.js');
require('./offline_verify.js');
var V = global.OfflineVerify;

// ── Shared contract with games/puzzles/core/client_spec.py ────────────────
var contract = require(path.join(__dirname, '..', '..', 'contracts', 'client_spec.json'));

async function run() {
  assert.ok(contract.answers.length > 0);
  for (var i = 0; i < contract.answers.length; i++) {
    var entry = contract.answers[i];
    for (var j = 0; j < entry.cases.length; j++) {
      var c = entry.cases[j];
      assert.strictEqual(
        await V.verify(entry.spec, c.text), c.status,
        entry.checker + ' ' + JSON.stringify(c.text),
      );
    }
  }
  var ladder = contract.ladder;
  assert.ok(ladder.cases.length > 0);
  for (var k = 0; k < ladder.cases.length; k++) {
    var w = ladder.cases[k];
    assert.strictEqual(
      await V.verifyWord(ladder.spec, w.word_index, w.word), w.accepted,
      'ladder ' + w.word_index + ' ' + JSON.stringify(w.word),
    );
  }

  // ── Specs from a newer server are refused instead of misread ─────────────
  assert.throws(function () { V.verify({ v: 2, kind: 'any' }, 'x'); }, /version/);
  await assert.rejects(V.verify({ v: 1, kind: 'raddle' }, 'x'), /not an answer spec/);

  console.log('offline_verify.test.js: ok');
}

run().catch(function (err) {
  console.error(err);
  process.exit(1);
});
