'use strict';

var assert = require('assert');
require('./raddle_masked_input.js');
var M = global.RaddleMaskedInput;

function testSlotCount() {
  assert.strictEqual(M.slotCount('#####-#########'), 14);
  assert.strictEqual(M.slotCount('### ###'), 6);
  assert.strictEqual(M.slotCount(''), 0);
}

function testExtractRussianLetters() {
  assert.strictEqual(M.extractRussianLetters('САНКТ-ПЕТЕРБУРГ'), 'САНКТПЕТЕРБУРГ');
  assert.strictEqual(M.extractRussianLetters('abc САНКТ 123'), 'САНКТ');
  assert.strictEqual(M.extractRussianLetters('ёжик'), 'ЕЖИК');
  assert.strictEqual(M.extractRussianLetters(''), '');
  assert.strictEqual(M.extractRussianLetters('Hello world'), '');
}

function testLettersToDisplayHyphenAfterFive() {
  var fmt = '#####' + '-' + '#########';
  assert.strictEqual(M.lettersToDisplay(fmt, ''), '');
  assert.strictEqual(M.lettersToDisplay(fmt, 'С'), 'С');
  assert.strictEqual(M.lettersToDisplay(fmt, 'САНК'), 'САНК');
  assert.strictEqual(M.lettersToDisplay(fmt, 'САНКТ'), 'САНКТ');
  assert.strictEqual(M.lettersToDisplay(fmt, 'САНКТП'), 'САНКТ-П');
  assert.strictEqual(
    M.lettersToDisplay(fmt, 'САНКТПЕТЕРБУРГ'),
    'САНКТ-ПЕТЕРБУРГ',
  );
}

function testLettersToDisplaySpace() {
  var fmt = '### ####';
  assert.strictEqual(M.lettersToDisplay(fmt, 'НЬЮ'), 'НЬЮ');
  assert.strictEqual(M.lettersToDisplay(fmt, 'НЬЮЙ'), 'НЬЮ Й');
  assert.strictEqual(M.lettersToDisplay(fmt, 'НЬЮЙОРК'), 'НЬЮ ЙОРК');
}

function testLettersToDisplayCommaAndApostrophe() {
  var fmt = '##, ##';
  assert.strictEqual(M.lettersToDisplay(fmt, 'А'), 'А');
  assert.strictEqual(M.lettersToDisplay(fmt, 'АБ'), 'АБ');
  assert.strictEqual(M.lettersToDisplay(fmt, 'АБВ'), 'АБ, В');
  assert.strictEqual(M.lettersToDisplay(fmt, "О"), "О");
  assert.strictEqual(M.lettersToDisplay("#'#", "ОК"), "О'К");
}

function testLettersToDisplayLeadingSeparator() {
  var fmt = '-###';
  assert.strictEqual(M.lettersToDisplay(fmt, ''), '-');
  assert.strictEqual(M.lettersToDisplay(fmt, 'А'), '-А');
}

function testSetLettersCapsAtMax() {
  var input = {
    value: '',
    dataset: {},
    getAttribute: function (name) {
      if (name === 'data-raddle-format') return '####';
      return null;
    },
    setSelectionRange: function () {},
  };
  var completed = [];
  M.setLetters(input, 'абвгдежз', {
    onComplete: function (_input, letters) { completed.push(letters); },
  });
  assert.strictEqual(input.dataset.raddleLetters, 'АБВГ');
  assert.strictEqual(input.value, 'АБВГ');
  assert.strictEqual(completed.length, 0);
  M.setLetters(input, 'АБВГ', {
    onComplete: function (_input, letters) { completed.push(letters); },
  });
  assert.deepStrictEqual(completed, ['АБВГ']);
}

function testPasteOverflowTruncates() {
  var input = {
    value: '',
    dataset: {},
    getAttribute: function (name) {
      if (name === 'data-raddle-format') return '###';
      return null;
    },
    setSelectionRange: function () {},
  };
  M.setLetters(input, 'САНКТ-ПЕТЕРБУРГ', {});
  assert.strictEqual(input.dataset.raddleLetters, 'САН');
  assert.strictEqual(input.value, 'САН');
}

function makeBoundInputMock(fmt) {
  var events = {};
  return {
    input: {
      value: '',
      dataset: {},
      selectionStart: 0,
      selectionEnd: 0,
      getAttribute: function (name) {
        if (name === 'data-raddle-format') return fmt;
        return null;
      },
      setAttribute: function () {},
      removeAttribute: function () {},
      setSelectionRange: function (start, end) {
        this.selectionStart = start;
        this.selectionEnd = end;
      },
      addEventListener: function (type, fn) {
        events[type] = fn;
      },
    },
    fireKeydown: function (key) {
      events.keydown({
        key: key,
        ctrlKey: false,
        metaKey: false,
        altKey: false,
        preventDefault: function () {},
      });
    },
  };
}

function testClearOnSelectionDelete() {
  var mock = makeBoundInputMock('#####');
  M.bindInput(mock.input, {});
  M.setLetters(mock.input, 'САНКТ', {});
  mock.input.selectionStart = 0;
  mock.input.selectionEnd = mock.input.value.length;
  mock.fireKeydown('Delete');
  assert.strictEqual(mock.input.dataset.raddleLetters, '');
  assert.strictEqual(mock.input.value, '');
}

function testClearOnSelectionBackspace() {
  var mock = makeBoundInputMock('#####');
  M.bindInput(mock.input, {});
  M.setLetters(mock.input, 'САНК', {});
  mock.input.selectionStart = 0;
  mock.input.selectionEnd = mock.input.value.length;
  mock.fireKeydown('Backspace');
  assert.strictEqual(mock.input.dataset.raddleLetters, '');
}

function testBackspaceWithoutSelectionRemovesOne() {
  var mock = makeBoundInputMock('#####');
  M.bindInput(mock.input, {});
  M.setLetters(mock.input, 'САНК', {});
  mock.fireKeydown('Backspace');
  assert.strictEqual(mock.input.dataset.raddleLetters, 'САН');
}

testSlotCount();
testExtractRussianLetters();
testLettersToDisplayHyphenAfterFive();
testLettersToDisplaySpace();
testLettersToDisplayCommaAndApostrophe();
testLettersToDisplayLeadingSeparator();
testSetLettersCapsAtMax();
testPasteOverflowTruncates();
testClearOnSelectionDelete();
testClearOnSelectionBackspace();
testBackspaceWithoutSelectionRemovesOne();

console.log('raddle_masked_input.test.js: ok');
