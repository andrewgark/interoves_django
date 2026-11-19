'use strict';

var assert = require('assert');
var B = require('./replacements_board.js');

function slot(key, encoded, guess, opts) {
  opts = opts || {};
  return {
    key: key,
    encoded: encoded,
    wordKey: B.normWord(encoded),
    correct: !!opts.correct,
    answer: opts.answer || '',
    guess: opts.correct ? '' : (guess || ''),
  };
}

// normWord: trim, collapse spaces, upper case (Russian included).
assert.strictEqual(B.normWord('  bar\u00a0 baz '), 'BAR BAZ');
assert.strictEqual(B.normWord('ёлка'), 'ЁЛКА');

function ids(options) { return options.map(function (o) { return o.id; }); }

// The user's example: three FOO slots, two with BAR and one with BAZ.
var example = [
  slot('0:0', 'FOO', 'BAR'),
  slot('1:0', 'FOO', 'bar '),
  slot('2:0', 'FOO', 'BAZ'),
  slot('2:1', 'QUX', ''),
];
var opts = B.scopeOptions(example, '0:0');
assert.deepStrictEqual(ids(opts), ['here', 'same', 'all']);
assert.deepStrictEqual(opts[1].keys, ['0:0', '1:0']);
assert.strictEqual(opts[1].label, 'Все FOO с «BAR» (2)');
assert.deepStrictEqual(opts[2].keys, ['0:0', '1:0', '2:0']);
assert.strictEqual(opts[2].label, 'Все нерешённые FOO (3)');
assert.strictEqual(B.defaultScope(opts, ''), 'same');
assert.strictEqual(B.defaultScope(opts, 'here'), 'here');
assert.strictEqual(B.defaultScope(opts, 'all'), 'same');

// Only this slot holds BAZ: "same value" equals "only here" and is dropped.
opts = B.scopeOptions(example, '2:0');
assert.deepStrictEqual(ids(opts), ['here', 'all']);
assert.strictEqual(B.defaultScope(opts, 'same'), 'here');

// Empty slots, correct ones excluded; "same" equals "all" and is dropped.
var empties = [
  slot('0:0', 'FOO', ''),
  slot('1:0', 'FOO', ''),
  slot('2:0', 'FOO', '', { correct: true, answer: 'BAR' }),
];
opts = B.scopeOptions(empties, '0:0');
assert.deepStrictEqual(ids(opts), ['here', 'all']);
assert.strictEqual(B.defaultScope(opts, ''), 'all');
assert.strictEqual(B.defaultScope(opts, 'here'), 'here');

// "Все пустые" label when same differs from all.
opts = B.scopeOptions([slot('0:0', 'FOO', ''), slot('1:0', 'FOO', ''), slot('2:0', 'FOO', 'X')], '0:0');
assert.strictEqual(opts[1].label, 'Все пустые FOO (2)');

// A single occurrence: one option, the row is hidden.
assert.deepStrictEqual(ids(B.scopeOptions([slot('0:0', 'FOO', 'A')], '0:0')), ['here']);
assert.strictEqual(B.scopeOptions(empties, '2:0').length, 0);

// Word list: sorted, deduplicated (case-insensitive), correct value shown once with a dot.
var model = B.chipModel([
  slot('0:0', 'яблоко', 'груша'),
  slot('0:1', 'FOO', '', { correct: true, answer: 'Bar' }),
  slot('1:0', 'foo', 'BAR'),
  slot('1:1', 'FOO', 'baz'),
  slot('1:2', 'FOO', ''),
  slot('2:0', 'АРБУЗ', '', { correct: true, answer: 'ДЫНЯ' }),
]);
// Russian collation sorts Cyrillic before Latin.
assert.deepStrictEqual(model.map(function (w) { return w.display; }), ['АРБУЗ', 'ЯБЛОКО', 'FOO']);
assert.deepStrictEqual(model[2].guesses, [
  { value: 'BAR', correct: true, unchecked: true },
  { value: 'BAZ', correct: false, unchecked: true },
]);
assert.strictEqual(model[2].allCorrect, false);
assert.strictEqual(model[0].allCorrect, true);

// Chip word click: empty slots first, otherwise all unsolved; nothing when all correct.
var target = B.chipWordTarget(example.concat([slot('3:0', 'FOO', '')]), 'FOO');
assert.deepStrictEqual(target.keys, ['3:0']);
assert.strictEqual(target.label, 'Заполнит пустые FOO (1)');
target = B.chipWordTarget(example, 'FOO');
assert.deepStrictEqual(target.keys, ['0:0', '1:0', '2:0']);
assert.strictEqual(target.label, 'Заменит нерешённые FOO (3)');
assert.strictEqual(B.chipWordTarget([slot('0:0', 'FOO', '', { correct: true, answer: 'X' })], 'FOO'), null);

// Chip guess click: unsolved slots holding that guess.
target = B.chipGuessTarget(example, 'FOO', 'bar');
assert.deepStrictEqual(target.keys, ['0:0', '1:0']);
assert.strictEqual(target.label, 'Заменит FOO с «BAR» (2)');
assert.strictEqual(B.chipGuessTarget(empties, 'FOO', 'BAR'), null);

// Common guess pre-fills the chip editor only when all targets agree.
assert.strictEqual(B.commonGuess(example, ['0:0', '1:0']), 'BAR');
assert.strictEqual(B.commonGuess(example, ['0:0', '2:0']), '');

// Changes skip correct slots; an empty value clears.
assert.deepStrictEqual(B.buildChanges(empties, ['0:0', '2:0'], ' qwe '), { '0:0': 'qwe' });
assert.deepStrictEqual(B.buildChanges(empties, ['0:0', '1:0'], ''), { '0:0': null, '1:0': null });
assert.deepStrictEqual(
  B.applyChanges({ '0:0': 'A', '0:1': 'B' }, { '0:0': null, '1:0': 'C' }),
  { '0:1': 'B', '1:0': 'C' },
);

console.log('replacements_board.test.js: ok');
