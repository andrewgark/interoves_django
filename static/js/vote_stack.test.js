'use strict';
global.window = global;

var assert = require('assert');
require('./vote_stack.js');
var V = global.InterovesVoteStack;

// Same table as games.tests.test_vote_count (server filter vote_count).
[
  [0, '0'], [999, '999'], [1000, '1K'], [1099, '1K'], [1234, '1,2K'],
  [1999, '1,9K'], [9999, '9,9K'], [10000, '10K'], [12345, '12K'],
  [999999, '999K'], [1000000, '1M'], [1250000, '1,2M'], [12500000, '12M'],
].forEach(function (row) {
  assert.strictEqual(V.shortCount(row[0]), row[1], 'shortCount(' + row[0] + ')');
});

function el(text, attrs) {
  attrs = attrs || {};
  return {
    textContent: text,
    getAttribute: function (k) { return k in attrs ? attrs[k] : null; },
    setAttribute: function (k, v) { attrs[k] = String(v); },
    removeAttribute: function (k) { delete attrs[k]; },
    attrs: attrs,
  };
}

// A page wrote a raw number after voting; another count is already short.
var up = el('1234');
var down = el('1,2K', { 'data-count': '1250' });
var vote = el('', {});
vote.querySelectorAll = function (sel) {
  return sel === '.new-vote__count' ? [up, down] : [];
};
global.document = { querySelectorAll: function () { return [vote]; } };
V.refresh();
assert.strictEqual(up.textContent, '1,2K');
assert.strictEqual(up.attrs['data-count'], '1234');
assert.strictEqual(down.textContent, '1,2K');
// Lead uses exact numbers, not the shortened labels.
assert.strictEqual(vote.attrs['data-lead'], 'down');

console.log('vote_stack.test.js: ok');
