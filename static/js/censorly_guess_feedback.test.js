var assert = require('assert');
var Feedback = require('./censorly_guess_feedback.js');

assert.strictEqual(
  Feedback.feedback({ status: 'duplicate', guess_word: 'кот' }, 'Кот'),
  'Слово «кот» уже вводили'
);
assert.strictEqual(
  Feedback.feedback({ status: 'already_open', guess_word: 'коты' }, 'Коты'),
  'Слово «коты» уже открыто'
);
assert.strictEqual(
  Feedback.feedback({ status: 'initially_open' }, 'и'),
  'Слово «и» уже открыто'
);
assert.strictEqual(
  Feedback.feedback({ status: 'invalid', error: 'Введите одно слово' }, '???'),
  'Введите одно слово'
);

['duplicate', 'initially_open', 'already_open'].forEach(function (status) {
  assert.strictEqual(Feedback.shouldClearInput(status), true);
});
['invalid', 'error', 'miss', 'hit'].forEach(function (status) {
  assert.strictEqual(Feedback.shouldClearInput(status), false);
});

console.log('censorly guess feedback tests passed');
