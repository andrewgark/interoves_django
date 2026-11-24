'use strict';

var assert = require('assert');
var GuessResponse = require('./alphabetty_guess_response.js');

assert.strictEqual(
  GuessResponse.shouldRecoverDuplicate([], ['ГОД'], 'ГОД'),
  true,
  'a server-confirmed word missing from the local board must repair stale UI'
);
assert.strictEqual(
  GuessResponse.shouldRecoverDuplicate(['ГОД'], ['ГОД'], 'ГОД'),
  false,
  'an intentional repeat of an already visible word remains a duplicate error'
);
assert.strictEqual(
  GuessResponse.shouldRecoverDuplicate([], [], 'ГОД'),
  false,
  'a malformed duplicate response must not be treated as confirmed state'
);
assert.strictEqual(GuessResponse.isHtmlBody('<!DOCTYPE html><html></html>'), true);
assert.strictEqual(GuessResponse.isHtmlBody('\n<!doctype html>'), true);
assert.strictEqual(GuessResponse.isHtmlBody('{"status":"later"}'), false);
assert.strictEqual(
  GuessResponse.transportErrorMessage(),
  'Не удалось проверить слово. Попробуйте ещё раз.'
);

(async function () {
  // A fetch that never settles is cut off: the form must get its button back.
  var seenSignal = null;
  var stalled = GuessResponse.fetchWithTimeout(function (url, init) {
    seenSignal = init.signal;
    assert.strictEqual(init.method, 'POST', 'request options pass through');
    return new Promise(function () {});
  }, '/alphabetty/1/guess/', { method: 'POST' }, 20);
  await assert.rejects(stalled, function (err) {
    return err.timedOut === true && err.message === GuessResponse.transportErrorMessage();
  });
  assert.ok(seenSignal && seenSignal.aborted, 'the stalled request is aborted');

  var answered = await GuessResponse.fetchWithTimeout(function () {
    return Promise.resolve('response');
  }, '/x', {}, 1000);
  assert.strictEqual(answered, 'response');

  await assert.rejects(
    GuessResponse.fetchWithTimeout(function () { return Promise.reject(new Error('offline')); }, '/x', {}, 1000),
    function (err) { return err.message === 'offline' && !err.timedOut; }
  );

  // A synchronous throw from fetch becomes a rejection, never a stuck page.
  await assert.rejects(
    GuessResponse.fetchWithTimeout(function () { throw new TypeError('bad'); }, '/x', {}, 1000),
    TypeError
  );

  console.log('alphabetty guess response tests passed');
})().catch(function (err) {
  console.error(err);
  process.exit(1);
});
