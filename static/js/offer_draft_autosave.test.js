'use strict';

var assert = require('assert');
require('./offer_draft_autosave.js');
var Autosave = global.OfferDraftAutosave;

function memoryStorage() {
  var values = {};
  return {
    getItem: function (key) { return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : null; },
    setItem: function (key, value) { values[key] = String(value); },
    removeItem: function (key) { delete values[key]; },
    values: values
  };
}

function tick() {
  return new Promise(function (resolve) { setTimeout(resolve, 0); });
}

(async function () {
  var storage = memoryStorage();
  var payload = { word: 'СЛОВО', comment: '' };
  var saves = [];
  var statuses = [];
  var timers = [];
  var restored = null;
  var controller = Autosave.create({
    storage: storage,
    storagePrefix: 'test:alphabetty',
    getPayload: function () { return payload; },
    restorePayload: function (value) { restored = value; payload = value; },
    save: function (id, value) {
      saves.push({ id: id, payload: value });
      return Promise.resolve({ offer: value });
    },
    canSync: function (value) { return !!value.word; },
    onStatus: function (state) { statuses.push(state); },
    setTimer: function (fn) { timers.push(fn); return timers.length - 1; },
    clearTimer: function (id) { timers[id] = null; }
  });

  assert.strictEqual(controller.open(7, payload), false);
  assert.strictEqual(statuses.pop(), 'idle');

  payload = { word: 'ДРУГОЕ', comment: 'заметка' };
  controller.changed();
  assert.strictEqual(statuses.pop(), 'pending');
  assert.ok(storage.getItem('test:alphabetty:7'), 'change is stored synchronously');
  timers.filter(Boolean).pop()();
  await tick();
  assert.deepStrictEqual(saves, [{ id: 7, payload: payload }]);
  assert.strictEqual(statuses.pop(), 'saved');
  assert.strictEqual(storage.getItem('test:alphabetty:7'), null, 'server save clears local draft');

  payload = { word: '', comment: 'ещё пишу' };
  controller.changed();
  assert.strictEqual(statuses.pop(), 'local', 'temporarily invalid content stays local');
  assert.strictEqual(saves.length, 1);
  await controller.close();
  assert.strictEqual(saves.length, 1, 'closing does not send invalid content');

  payload = { word: 'server', comment: '' };
  assert.strictEqual(controller.open(7, payload), true);
  assert.deepStrictEqual(restored, { word: '', comment: 'ещё пишу' });
  assert.strictEqual(statuses.pop(), 'restored');

  payload = { word: 'ГОТОВО', comment: 'ещё пишу' };
  controller.changed();
  await controller.flush();
  assert.strictEqual(saves.length, 2);
  assert.deepStrictEqual(saves[1].payload, payload);

  payload = { word: 'ЗАКРЫТИЕ', comment: 'сразу после ввода' };
  controller.changed();
  await controller.close();
  assert.strictEqual(saves.length, 3, 'closing flushes a valid draft without waiting for debounce');
  assert.deepStrictEqual(saves[2].payload, payload);

  var failingStorage = {
    getItem: function () { return null; },
    setItem: function () { throw new Error('quota'); },
    removeItem: function () {}
  };
  var failureState = '';
  var failing = Autosave.create({
    storage: failingStorage,
    getPayload: function () { return { word: 'ТЕСТ' }; },
    save: function () { return Promise.reject(new Error('offline')); },
    onStatus: function (state) { failureState = state; },
    delayMs: 0
  });
  failing.open(1, { word: 'СЛОВО' });
  failing.changed();
  await tick();
  await tick();
  assert.strictEqual(failureState, 'error', 'UI must not claim a local save when storage failed');

  console.log('offer_draft_autosave.test.js: ok');
})().catch(function (error) {
  console.error(error);
  process.exitCode = 1;
});
