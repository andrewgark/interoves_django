'use strict';

var assert = require('assert');
var P = require('./replacements_input_parse.js');

function testElevenQuotedDocStyle() {
  var w = [];
  var i;
  for (i = 0; i < 11; i++) w.push('"СЛОВО' + i + '"');
  var line = w.slice(0, 3).join(', ') + '  и ' + w.slice(3, 6).join(', ') + ' - примерно так звучит ';
  line += w.slice(6, 11).join(', ') + ' на языках. "КАК ДЕЛА?"';
  var got = P.parseReplLineAnswersSmart(null, line, 11);
  assert.strictEqual(got.length, 11);
  for (i = 0; i < 11; i++) assert.strictEqual(got[i], 'СЛОВО' + i);
}

function testUserSnippetFourSlots() {
  var userSnippet =
    '"КОМАН СОВА", "ХАУ Ю ФИЛИН"  и "ХАУ ДЮ Ю ДЮ" - примерно так звучит "КАК ДЕЛА?" на разных языках.';
  var g4 = P.parseReplLineAnswersSmart(null, userSnippet, 4);
  assert.deepStrictEqual(g4, ['КОМАН СОВА', 'ХАУ Ю ФИЛИН', 'ХАУ ДЮ Ю ДЮ', 'КАК ДЕЛА?']);
}

function testLeadingBlankLines() {
  var w = [];
  var i;
  for (i = 0; i < 11; i++) w.push('"W' + i + '"');
  var line = w.join(' и ');
  var multiline = '\n\n  \n' + line + '\n';
  var got = P.parseReplLineAnswersSmart(null, multiline, 11);
  assert.strictEqual(got.length, 11);
  for (i = 0; i < 11; i++) assert.strictEqual(got[i], 'W' + i);
}

function testMixedGuillemetAndAscii() {
  var mixed = '«А» и "Б" , «В»';
  var g3 = P.parseReplLineAnswersSmart(null, mixed, 3);
  assert.deepStrictEqual(g3, ['А', 'Б', 'В']);
}

function testTypographicQuotesNormalized() {
  var s = '\u201CКОТ\u201D, \u201CПЕС\u201D';
  var g2 = P.parseReplLineAnswersSmart(null, s, 2);
  assert.deepStrictEqual(g2, ['КОТ', 'ПЕС']);
}

testElevenQuotedDocStyle();
testUserSnippetFourSlots();
testLeadingBlankLines();
testMixedGuillemetAndAscii();
testTypographicQuotesNormalized();

(function testFourQuotesExpandToEleven() {
  var s =
    '"КОМАН СОВА", "ХАУ Ю ФИЛИН"  и "ХАУ ДЮ Ю ДЮ" - примерно так звучит ' +
    '"КАК ДЕЛА?" на разных языках.';
  var got = P.parseReplLineAnswersSmart(null, s, 11);
  assert.deepStrictEqual(got, [
    'КОМАН', 'СОВА', 'ХАУ', 'Ю', 'ФИЛИН', 'ХАУ', 'ДЮ', 'Ю', 'ДЮ', 'КАК', 'ДЕЛА',
  ]);
})();

console.log('replacements_input_parse.test.js: ok');
