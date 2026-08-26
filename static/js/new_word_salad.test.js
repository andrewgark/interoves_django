'use strict';

var assert = require('assert');
require('./new_word_salad.js');
var Path = global.WordSaladPath;

function drag(indices) {
  return indices.reduce(function (path, index) {
    return Path.nextPath(path, index);
  }, []);
}

assert.strictEqual(Path.cellsAreAdjacent(0, 1), true);
assert.strictEqual(Path.cellsAreAdjacent(0, 5), true);
assert.strictEqual(Path.cellsAreAdjacent(0, 2), false);

assert.deepStrictEqual(Path.neighborPairs([]), []);
assert.deepStrictEqual(Path.neighborPairs([0]), []);
assert.deepStrictEqual(Path.neighborPairs([0, 1]), [[0, 1]]);
assert.deepStrictEqual(Path.neighborPairs([0, 5]), [[0, 5]]);
assert.deepStrictEqual(Path.neighborPairs([0, 2]), []);
assert.deepStrictEqual(Path.neighborPairs([5, 0, 0, 5]), [[0, 5]]);
assert.strictEqual(
  Path.neighborPairs([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]).length,
  42,
  'full 4×4 king-move graph has 42 edges'
);
assert.strictEqual(
  Path.neighborPairs([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]).length,
  39,
  'removing a corner cell drops its 3 remaining-neighbor edges'
);

assert.deepStrictEqual(drag([0]), [0]);
assert.deepStrictEqual(
  drag([0, 0, 0, 0]),
  [0],
  'holding the first cell must not toggle the letter off'
);
assert.deepStrictEqual(
  drag([0, 0, 0, 1]),
  [0, 1],
  'jitter on the first cell must not drop it before the neighbor is added'
);
assert.deepStrictEqual(drag([0, 1, 1, 1, 5]), [0, 1, 5]);

var held = Path.nextPath([0], 0);
assert.strictEqual(held, Path.nextPath(held, 0));

assert.deepStrictEqual(drag([0, 1, 2, 1]), [0, 1]);
assert.deepStrictEqual(drag([0, 2]), [0]);
assert.deepStrictEqual(Path.nextPath([0, 1], -1), [0, 1]);

function activeSet() {
  var active = {};
  Array.prototype.forEach.call(arguments, function (index) { active[index] = true; });
  return function (index) { return !!active[index]; };
}

assert.deepStrictEqual(
  Path.nextPath([0], 1, activeSet(0)),
  [0],
  'deleted cells must not be added to the path'
);
assert.deepStrictEqual(
  Path.startPress([], 3, activeSet(0, 1)).path,
  [],
  'a press on a deleted cell must not start a line'
);
assert.deepStrictEqual(
  Path.movePress([0], 5, false, activeSet(0, 1)).path,
  [0],
  'dragging across a deleted cell must not extend the line'
);

function press(path, index) {
  return Path.startPress(path, index);
}

(function testSecondClickOnSingleLetterClears() {
  var down = press([0], 0);
  assert.deepStrictEqual(down.path, [0]);
  assert.strictEqual(down.clearOnRelease, true);
  assert.deepStrictEqual(Path.endPress(down.path, down.clearOnRelease), []);
})();

(function testHoldAndDragFromSelectedLetterKeepsIt() {
  var down = press([0], 0);
  var moved = Path.movePress(down.path, 0, down.clearOnRelease);
  assert.strictEqual(moved.clearOnRelease, true);
  moved = Path.movePress(moved.path, 1, moved.clearOnRelease);
  assert.deepStrictEqual(moved.path, [0, 1]);
  assert.strictEqual(moved.clearOnRelease, false);
  assert.deepStrictEqual(Path.endPress(moved.path, moved.clearOnRelease), [0, 1]);
})();

(function testFirstPressJitterDoesNotClearOnRelease() {
  var down = press([], 0);
  assert.deepStrictEqual(down.path, [0]);
  assert.strictEqual(down.clearOnRelease, false);
  var moved = Path.movePress(down.path, 0, down.clearOnRelease);
  moved = Path.movePress(moved.path, 0, moved.clearOnRelease);
  assert.deepStrictEqual(Path.endPress(moved.path, moved.clearOnRelease), [0]);
})();

function escapeEvent(overrides) {
  var event = {
    key: 'Escape',
    defaultPrevented: false,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    target: { tagName: 'BODY', isContentEditable: false }
  };
  Object.keys(overrides || {}).forEach(function (key) { event[key] = overrides[key]; });
  return event;
}

(function testEscapeClearsWhenIdle() {
  assert.strictEqual(Path.shouldHandleEscape(escapeEvent()), true);
})();

(function testEscapeIgnoredInFieldsAndModals() {
  assert.strictEqual(Path.shouldHandleEscape(escapeEvent({ key: 'Enter' })), false);
  assert.strictEqual(Path.shouldHandleEscape(escapeEvent({ defaultPrevented: true })), false);
  assert.strictEqual(Path.shouldHandleEscape(escapeEvent({ ctrlKey: true })), false);
  assert.strictEqual(Path.shouldHandleEscape(escapeEvent({ target: { tagName: 'INPUT', isContentEditable: false } })), false);
  assert.strictEqual(Path.shouldHandleEscape(escapeEvent({ target: { tagName: 'TEXTAREA', isContentEditable: false } })), false);
  assert.strictEqual(
    Path.shouldHandleEscape(escapeEvent(), { querySelector: function () { return {}; } }),
    false,
    'an open modal must keep Escape'
  );
})();

(function testRememberExtraWord() {
  var first = Path.rememberExtra([], 'кот', {});
  assert.deepStrictEqual(first.words, ['КОТ']);
  assert.strictEqual(first.latest, 'КОТ');
  assert.strictEqual(first.changed, true);

  var again = Path.rememberExtra(['КОТ', 'ЛИСА'], 'кот', {});
  assert.deepStrictEqual(again.words, ['ЛИСА', 'КОТ']);
  assert.strictEqual(again.latest, 'КОТ');

  var skipped = Path.rememberExtra([], 'на', {});
  assert.strictEqual(skipped.changed, false);
  assert.deepStrictEqual(skipped.words, []);

  var answer = Path.rememberExtra([], 'кот', { КОТ: true });
  assert.strictEqual(answer.changed, false);
})();

(function testAnswerFeedback() {
  assert.deepStrictEqual(Path.feedbackForResult('correct', 'ёжик'), {
    word: 'ЁЖИК',
    message: 'Верно!',
    icon: 'ph-check-circle',
    duplicate: false
  });
  assert.deepStrictEqual(Path.feedbackForResult('duplicate', 'салат'), {
    word: 'САЛАТ',
    message: 'Уже было!',
    icon: 'ph-arrow-counter-clockwise',
    duplicate: true
  });
})();

console.log('new_word_salad.test.js: ok');
