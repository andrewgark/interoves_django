var assert = require('assert');
var punctuation = require('./censorly_punctuation.js');

function expect(text, previous, next, before, after) {
  assert.deepStrictEqual(
    punctuation.attachments(text, previous, next),
    {before: before, after: after},
    JSON.stringify({text: text, previous: previous, next: next})
  );
}

expect('«', false, true, false, true);
expect('»', true, false, true, false);
expect('«', true, true, false, true);
expect('«.', false, true, true, true);
expect('.«', true, true, true, true);
expect(')', true, false, true, false);
expect('(', false, true, false, true);
expect('"', false, true, false, true);
expect('"', true, true, true, false);
expect("'", false, true, false, true);
expect("'", true, true, true, false);

console.log('censorly punctuation tests passed');
