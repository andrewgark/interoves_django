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

console.log('new_word_salad.test.js: ok');
