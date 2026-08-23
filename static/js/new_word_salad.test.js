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

console.log('new_word_salad.test.js: ok');
