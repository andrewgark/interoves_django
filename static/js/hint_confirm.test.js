'use strict';

var assert = require('assert');
require('./hint_confirm.js');
var hints = global.InterovesHintConfirm;

[
  [0, 'баллов'],
  [1, 'балл'],
  [2, 'балла'],
  [4, 'балла'],
  [5, 'баллов'],
  [11, 'баллов'],
  [14, 'баллов'],
  [21, 'балл'],
  [22, 'балла'],
  [25, 'баллов'],
  [0.5, 'балла'],
  ['1,5', 'балла'],
  ['2.0', 'балла'],
].forEach(function (item) {
  assert.strictEqual(hints.pointsWord(item[0]), item[1], String(item[0]));
});

assert.strictEqual(
  hints.message(5),
  'Снимется 5 баллов. Баллы за задание не опустятся ниже нуля.'
);

console.log('hint_confirm.test.js: ok');
