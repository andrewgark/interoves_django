'use strict';

var assert = require('assert');
var card = require('./daily_share_card.js');

function payload(overrides) {
  var base = {
    kind: 'ladder',
    game_kind: 'ladder',
    renderer_version: '1',
    locale: 'ru',
    number: '46',
    seed: 46,
    title: 'Лесенка #46',
    date_label: '3 сентября 2026',
    headline: 'Лесенка · 4:32',
    stats_line: 'без подсказок',
    brand: 'interoves.com',
    elapsed_compact: '4:32',
    steps: [
      { role: 'start', length: 5, state: 'given' },
      { role: 'middle', length: 6, state: 'green' },
      { role: 'middle', length: 5, state: 'yellow' },
      { role: 'end', length: 5, state: 'given' },
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

(function testNoSpoilerText() {
  var svg = card.buildShareCardSvg(payload());
  assert.ok(svg.indexOf('ПАРИЖ') === -1);
  assert.ok(svg.indexOf('ДАКАР') === -1);
  assert.ok(svg.indexOf('МОСКВА') === -1);
  assert.ok(svg.indexOf('secret') === -1);
})();

(function testRuAndEnDoNotThrow() {
  var ru = card.buildShareCardSvg(payload({ locale: 'ru' }));
  var en = card.buildShareCardSvg(payload({
    locale: 'en',
    title: 'Ladder #46',
    date_label: 'September 3, 2026',
    headline: 'Ladder · 4:32',
    stats_line: 'no hints',
  }));
  assert.ok(ru.indexOf('Лесенка') !== -1);
  assert.ok(en.indexOf('Ladder') !== -1);
})();

(function testLongStringsWrap() {
  var svg = card.buildShareCardSvg(payload({
    locale: 'en',
    title: 'Alphabetty #999999999999',
    date_label: 'September 3, 2026',
    headline: 'Alphabetty completed in 12:00:04',
    stats_line: '12345 tries · no hints at all in this extra long stats line',
    kind: 'alphabetty',
    game_kind: 'alphabetty',
    variant: 0,
  }));
  assert.ok(svg.indexOf('<svg') === 0);
  assert.ok(svg.indexOf('12345 tries') !== -1);
})();

(function testSaladAndAlphabettyKinds() {
  var salad = card.buildShareCardSvg(payload({
    kind: 'salad',
    game_kind: 'salad',
    title: 'Салатик #23',
    headline: 'Салатик · 6:17',
    word_results: [{ hint_count: 0 }, { hint_count: 2 }, { hint_count: 0 }],
    word_count: 3,
  }));
  var ab = card.buildShareCardSvg(payload({
    kind: 'alphabetty',
    game_kind: 'alphabetty',
    title: 'Алфавитка #31',
    headline: 'Алфавитка · 2:08',
    variant: 1,
    steps: [],
  }));
  assert.ok(salad.indexOf('Салатик') !== -1);
  assert.ok(ab.indexOf('Алфавитка') !== -1);
})();

(function testDecorationIsSeeded() {
  var a = card.buildShareCardSvg(payload({ kind: 'alphabetty', game_kind: 'alphabetty', seed: 10, variant: 0, steps: [] }));
  var b = card.buildShareCardSvg(payload({ kind: 'alphabetty', game_kind: 'alphabetty', seed: 10, variant: 0, steps: [] }));
  var c = card.buildShareCardSvg(payload({ kind: 'alphabetty', game_kind: 'alphabetty', seed: 11, variant: 0, steps: [] }));
  assert.strictEqual(a, b);
  assert.notStrictEqual(a, c);
})();
