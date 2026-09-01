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

console.log('alphabetty guess response tests passed');
