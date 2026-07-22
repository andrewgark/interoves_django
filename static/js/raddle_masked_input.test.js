'use strict';

var assert = require('assert');
require('./imask.min.js');
require('./raddle_masked_input.js');
var M = global.RaddleMaskedInput;
var IMask = global.IMask;

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

function testExtractLatinLetters() {
  assert.strictEqual(M.extractLatinLetters('NEW-YORK'), 'NEWYORK');
  assert.strictEqual(M.extractLatinLetters('abc ПРИВЕТ 123'), 'ABC');
  assert.strictEqual(M.extractLatinLetters("o'kay"), 'OKAY');
  assert.strictEqual(M.extractLatinLetters(''), '');
  assert.strictEqual(M.extractLetters('hello', 'latin'), 'HELLO');
  assert.strictEqual(M.extractLetters('привет', 'cyrillic'), 'ПРИВЕТ');
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
  assert.strictEqual(M.lettersToDisplay(fmt, ''), '');
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
  assert.strictEqual(completed.length, 1);
  completed.length = 0;
  M.setLetters(input, 'АБ', {
    onComplete: function (_input, letters) { completed.push(letters); },
  });
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

function makeImaskInput(fmt, script) {
  var el = {
    type: 'text',
    value: '',
    selectionStart: 0,
    selectionEnd: 0,
    dataset: {},
    _attrs: { 'data-raddle-format': fmt },
    getAttribute: function (name) {
      return this._attrs[name] || null;
    },
    setAttribute: function (name, val) {
      this._attrs[name] = val;
    },
    removeAttribute: function (name) {
      delete this._attrs[name];
    },
    setSelectionRange: function (start, end) {
      this.selectionStart = start;
      this.selectionEnd = end;
    },
    addEventListener: function () {},
    removeEventListener: function () {},
    focus: function () {},
    blur: function () {},
    ownerDocument: { activeElement: null },
    getRootNode: function () { return { activeElement: null }; },
  };
  if (script) el._attrs['data-raddle-script'] = script;
  return el;
}

function testImaskFormatsMatchServerTemplate() {
  var cases = [
    ['#####-#########', 'САНКТПЕТЕРБУРГ', 'САНКТ-ПЕТЕРБУРГ'],
    ['### ####', 'НЬЮЙОРК', 'НЬЮ ЙОРК'],
    ['#,#', 'АБ', 'А,Б'],
    ["#'###", 'ОКЕЙ', "О'КЕЙ"],
  ];
  cases.forEach(function (item) {
    var mask = IMask.createMask(M.buildMaskOptions(item[0]));
    mask.unmaskedValue = item[1];
    assert.strictEqual(mask.value, item[2], item[0]);
    assert.strictEqual(mask.unmaskedValue, item[1], item[0] + ' unmasked');
  });
}

function testImaskLatinFormats() {
  var mask = IMask.createMask(M.buildMaskOptions('###-####', 'latin'));
  mask.unmaskedValue = 'NEWYORK';
  assert.strictEqual(mask.value, 'NEW-YORK');
  assert.strictEqual(mask.unmaskedValue, 'NEWYORK');
}

function testBindInputUsesImaskForEditing() {
  var input = makeImaskInput('#####');
  M.bindInput(input, {});
  M.setLetters(input, 'САНКТ', {});
  assert.strictEqual(M.getSubmitValue(input), 'САНКТ');
  assert.strictEqual(input.value, 'САНКТ');

  M.setLetters(input, 'САТ', {});
  assert.strictEqual(M.getSubmitValue(input), 'САТ');
  assert.strictEqual(input.value, 'САТ');

  M.setLetters(input, '', {});
  assert.strictEqual(M.getSubmitValue(input), '');
  assert.strictEqual(input.value, '');
}

function testBindInputLatinRejectsCyrillic() {
  var input = makeImaskInput('#####', 'latin');
  M.bindInput(input, {});
  M.setLetters(input, 'hello', {});
  assert.strictEqual(M.getSubmitValue(input), 'HELLO');
  assert.strictEqual(input.value, 'HELLO');
  M.setLetters(input, 'привет', {});
  assert.strictEqual(M.getSubmitValue(input), '');
  assert.strictEqual(input.value, '');
}

function testBindInputMixedAllowsBoth() {
  var input = makeImaskInput('#####', 'mixed');
  M.bindInput(input, {});
  M.setLetters(input, 'hello', {});
  assert.strictEqual(M.getSubmitValue(input), 'HELLO');
  M.setLetters(input, 'привет', {});
  assert.strictEqual(M.getSubmitValue(input), 'ПРИВЕ');
  M.setLetters(input, 'heлlo', {});
  assert.strictEqual(M.getSubmitValue(input), 'HEЛLO');
}

function testBindInputNormalizesYo() {
  var input = makeImaskInput('###');
  M.bindInput(input, {});
  M.setLetters(input, 'ёжик', {});
  assert.strictEqual(M.getSubmitValue(input), 'ЕЖИ');
}

testSlotCount();
testExtractRussianLetters();
testExtractLatinLetters();
testLettersToDisplayHyphenAfterFive();
testLettersToDisplaySpace();
testLettersToDisplayCommaAndApostrophe();
testLettersToDisplayLeadingSeparator();
testSetLettersCapsAtMax();
testPasteOverflowTruncates();
testImaskFormatsMatchServerTemplate();
testImaskLatinFormats();
testBindInputUsesImaskForEditing();
testBindInputLatinRejectsCyrillic();
testBindInputMixedAllowsBoth();
testBindInputNormalizesYo();

console.log('raddle_masked_input.test.js: ok');
