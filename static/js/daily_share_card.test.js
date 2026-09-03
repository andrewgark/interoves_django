'use strict';

var assert = require('assert');
var card = require('./daily_share_card.js');

function payload(overrides) {
  var base = {
    kind: 'ladder',
    game_kind: 'ladder',
    renderer_version: '3',
    locale: 'ru',
    number: '46',
    seed: 46,
    title: 'Лесенка #46',
    date_label: '3 сентября 2026',
    headline: 'Лесенка #46 решена за 4:32',
    stats_line: 'без подсказок',
    brand: 'interoves.com',
    elapsed_compact: '4:32',
    steps: [
      { role: 'start', length: 5, state: 'given', label: 'ПАРИЖ' },
      { role: 'middle', length: 6, state: 'green' },
      { role: 'middle', length: 5, state: 'yellow' },
      { role: 'end', length: 5, state: 'given', label: 'ДАКАР' },
    ],
  };
  Object.keys(overrides || {}).forEach(function (key) {
    base[key] = overrides[key];
  });
  return base;
}

(function testSvgSizeAndDeterminism() {
  var svg = card.buildShareCardSvg(payload());
  assert.ok(svg.indexOf('width="1080"') !== -1);
  assert.ok(svg.indexOf('height="1920"') !== -1);
  assert.strictEqual(svg, card.buildShareCardSvg(payload()));
})();

(function testLadderShowsPublicEndpointsOnly() {
  var svg = card.buildShareCardSvg(payload());
  assert.ok(svg.indexOf('ПАРИЖ') !== -1);
  assert.ok(svg.indexOf('ДАКАР') !== -1);
  assert.ok(svg.indexOf('МОСКВА') === -1);
  assert.ok(svg.indexOf('secret') === -1);
  assert.strictEqual(card.VERSION, '3');
})();

(function testHeaderDoesNotDuplicateTitle() {
  var svg = card.buildShareCardSvg(payload());
  var headlineHits = svg.split('Лесенка #46 решена за 4:32').length - 1;
  var titleHits = svg.split('Лесенка #46').length - 1;
  assert.strictEqual(headlineHits, 1);
  assert.strictEqual(titleHits, 1);
})();

(function testRuAndEnDoNotThrow() {
  var ru = card.buildShareCardSvg(payload({ locale: 'ru' }));
  var en = card.buildShareCardSvg(payload({
    locale: 'en',
    title: 'Ladder #46',
    date_label: 'September 3, 2026',
    headline: 'Ladder #46 solved in 4:32',
    stats_line: 'no hints',
  }));
  assert.ok(ru.indexOf('Лесенка #46 решена за 4:32') !== -1);
  assert.ok(en.indexOf('Ladder #46 solved in 4:32') !== -1);
})();

(function testLongStringsWrap() {
  var svg = card.buildShareCardSvg(payload({
    locale: 'en',
    title: 'Alphabetty #999999999999',
    date_label: 'September 3, 2026',
    headline: 'Alphabetty #999999999999 solved in 12:00:04',
    stats_line: 'no hints at all in this extra long stats line',
    kind: 'alphabetty',
    game_kind: 'alphabetty',
    variant: 0,
    attempts: 12345,
    steps: [],
  }));
  assert.ok(svg.indexOf('<svg') === 0);
  assert.ok(svg.indexOf('12345') !== -1);
  assert.ok(svg.indexOf('12:00:04') !== -1);
})();

(function testSaladGridLettersAndSingleResultRow() {
  var salad = card.buildShareCardSvg(payload({
    kind: 'salad',
    game_kind: 'salad',
    title: 'Салатик #23',
    headline: 'Салатик #23 решён за 6:17',
    grid: 'АБВГДЕЖЗИЙКЛМНОП'.split(''),
    word_results: [{ hint_count: 0 }, { hint_count: 2 }, { hint_count: 0 }, { hint_count: 1 }],
    word_count: 4,
    steps: [],
  }));
  assert.ok(salad.indexOf('Салатик #23 решён за 6:17') !== -1);
  assert.ok(salad.indexOf('>А<') !== -1);
  assert.ok(salad.indexOf('>П<') !== -1);
  assert.ok(salad.indexOf('МОСКВА') === -1);
})();

(function testAlphabettyUsesAttemptsAndRussianLetters() {
  var ab = card.buildShareCardSvg(payload({
    kind: 'alphabetty',
    game_kind: 'alphabetty',
    title: 'Алфавитка #31',
    headline: 'Алфавитка #31 решена за 2:08',
    variant: 1,
    attempts: 6,
    attempts_word: 'попыток',
    elapsed_compact: '2:08',
    steps: [],
  }));
  assert.ok(ab.indexOf('Алфавитка #31 решена за 2:08') !== -1);
  assert.ok(ab.indexOf('>6<') !== -1);
  assert.ok(ab.indexOf('попыток') !== -1);
  assert.ok(ab.indexOf('ABCDEF') === -1);
})();

(function testAlphabettyAttemptsWordCases() {
  var one = card.buildShareCardSvg(payload({
    kind: 'alphabetty', game_kind: 'alphabetty', attempts: 1, steps: [],
  }));
  var few = card.buildShareCardSvg(payload({
    kind: 'alphabetty', game_kind: 'alphabetty', attempts: 3, steps: [],
  }));
  var many = card.buildShareCardSvg(payload({
    kind: 'alphabetty', game_kind: 'alphabetty', attempts: 11, steps: [],
  }));
  assert.ok(one.indexOf('попытка') !== -1);
  assert.ok(few.indexOf('попытки') !== -1);
  assert.ok(many.indexOf('попыток') !== -1);
})();

(function testFooterIncludesLogoAndHost() {
  var svg = card.buildShareCardSvg(payload());
  assert.ok(svg.indexOf('data:image/png;base64,') !== -1);
  assert.ok(svg.indexOf('interoves.com') !== -1);
})();

(function testDecorationIsSeeded() {
  var a = card.buildShareCardSvg(payload({ kind: 'alphabetty', game_kind: 'alphabetty', seed: 10, variant: 0, steps: [], attempts: 4 }));
  var b = card.buildShareCardSvg(payload({ kind: 'alphabetty', game_kind: 'alphabetty', seed: 10, variant: 0, steps: [], attempts: 4 }));
  var c = card.buildShareCardSvg(payload({ kind: 'alphabetty', game_kind: 'alphabetty', seed: 11, variant: 0, steps: [], attempts: 4 }));
  assert.strictEqual(a, b);
  assert.notStrictEqual(a, c);
})();
