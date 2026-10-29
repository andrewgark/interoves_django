'use strict';

var assert = require('assert');
require('./offline_outbox.js');
var O = global.InterovesOutbox;

function fakeStorage() {
  var data = {};
  return {
    data: data,
    getItem: function (k) { return k in data ? data[k] : null; },
    setItem: function (k, v) { data[k] = String(v); },
    removeItem: function (k) { delete data[k]; },
  };
}

async function run() {
  // ── Entries persist in storage, in order, once per id ───────────────────
  var storage = fakeStorage();
  var box = O.createOutbox({ storage: storage });
  box.add({ id: 'a', scope: 7, fields: [['word', 'кот']] });
  box.add({ id: 'b', scope: 7, fields: [] });
  box.add({ id: 'a', scope: 7, fields: [] });
  box.add({ id: 'c', scope: 8, fields: [] });
  assert.deepStrictEqual(O.createOutbox({ storage: storage }).entries(7).map(function (e) { return e.id; }), ['a', 'b']);
  assert.strictEqual(box.size(), 3);

  // ── Flush sends in order and stops at the first move that must wait ─────
  var sent = [];
  var result = await O.flush(box, 7, function (entry) {
    sent.push(entry.id);
    return entry.id === 'b' ? 'retry' : 'sent';
  });
  assert.deepStrictEqual(sent, ['a', 'b']);
  assert.deepStrictEqual(result, { sent: 1, dropped: 0, left: 1 });
  assert.deepStrictEqual(box.entries(7).map(function (e) { return e.id; }), ['b']);

  // ── A failing send keeps the move; a dropped one is removed ─────────────
  result = await O.flush(box, 7, function () { return Promise.reject(new TypeError('offline')); });
  assert.strictEqual(result.left, 1);
  result = await O.flush(box, 7, function () { return 'drop'; });
  assert.deepStrictEqual(result, { sent: 0, dropped: 1, left: 0 });
  assert.strictEqual(box.size(7), 0);
  assert.strictEqual(box.size(8), 1);

  // ── Broken or full storage never throws ─────────────────────────────────
  var broken = O.createOutbox({ storage: {
    getItem: function () { return '{not json'; },
    setItem: function () { throw new Error('QuotaExceededError'); },
    removeItem: function () {},
  } });
  assert.deepStrictEqual(broken.entries(), []);
  assert.strictEqual(broken.add({ id: 'x', scope: 1 }), false);

  // ── Form fields keep strings only ───────────────────────────────────────
  var fd = new FormData();
  fd.append('word', 'кот');
  fd.append('file', new Blob(['x']));
  assert.deepStrictEqual(O.formFields(fd), [['word', 'кот']]);

  console.log('offline_outbox.test.js: ok');
}

run().catch(function (err) {
  console.error(err);
  process.exit(1);
});
