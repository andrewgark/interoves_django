'use strict';

var assert = require('assert');
require('./fetch_json.js');
var F = global.InterovesFetchJson;

assert.strictEqual(F.isAbortError({name: 'AbortError'}), true);
assert.strictEqual(F.isAbortError({name: 'TypeError'}), false);
assert.strictEqual(F.shouldRetry({name: 'AbortError'}, 1, 3), false);
assert.strictEqual(F.shouldRetry({name: 'TypeError'}, 1, 3), true);
assert.strictEqual(F.shouldRetry({name: 'TypeError'}, 3, 3), false);
assert.strictEqual(F.shouldRetry({retryable: false}, 1, 3), false);
assert.strictEqual(F.shouldRetry({retryable: true}, 1, 3), true);

var stale = {id: 'raddle-form-1-4'};
var fresh = {id: 'raddle-form-1-4', live: true};
var doc = {
  getElementById: function (id) {
    return id === 'raddle-form-1-4' ? fresh : null;
  },
  querySelector: function (selector) {
    if (selector.indexOf('data-word-index="4"') !== -1) {
      return {classList: {contains: function (name) { return name === 'new-raddle-row--solved'; }}};
    }
    return {classList: {contains: function () { return false; }}};
  },
};
assert.strictEqual(F.liveElement(stale, doc), fresh);
assert.strictEqual(F.wordRowIsSolved(doc, '9', '4'), true);
assert.strictEqual(F.wordRowIsSolved(doc, '9', '1'), false);

F.parseJsonResponse({
  status: 200,
  text: function () { return Promise.resolve('{"status":"ok"}'); },
}).then(function (data) {
  assert.deepStrictEqual(data, {status: 'ok'});
  return F.parseJsonResponse({
    status: 502,
    text: function () { return Promise.resolve('<html>bad gateway</html>'); },
  }).then(function () {
    assert.fail('expected non-json error');
  }, function (err) {
    assert.strictEqual(err.retryable, true);
    assert.strictEqual(err.httpStatus, 502);
    return F.parseJsonResponse({
      status: 403,
      text: function () { return Promise.resolve('<html>Forbidden</html>'); },
    }).then(function () {
      assert.fail('expected non-json error');
    }, function (forbidden) {
      assert.strictEqual(forbidden.retryable, false);
      assert.strictEqual(forbidden.httpStatus, 403);
      console.log('fetch_json.test.js: ok');
    });
  });
}).catch(function (err) {
  console.error(err);
  process.exit(1);
});
