'use strict';

var assert = require('assert');
var stats = require('./player_game_stats.js');

assert.strictEqual(stats.formatDuration(65), '1:05');
assert.strictEqual(stats.formatDuration(3725), '1:02:05');
assert.strictEqual(stats.formatValue('score', 87.4), '87%');
assert.strictEqual(stats.formatValue('att', 2.5), '2,5');
assert.strictEqual(stats.formatValue('time', null), '—');

assert.strictEqual(stats.verdict('time', 100, 200), 'better');
assert.strictEqual(stats.verdict('score', 50, 100), 'worse');
assert.strictEqual(stats.verdict('att', 3, 3), 'even');
assert.strictEqual(stats.verdict('time', null, 3), '');

assert.strictEqual(stats.plural(1, ['выпуск', 'выпуска', 'выпусков']), 'выпуск');
assert.strictEqual(stats.plural(12, ['выпуск', 'выпуска', 'выпусков']), 'выпусков');
assert.strictEqual(stats.plural(23, ['выпуск', 'выпуска', 'выпусков']), 'выпуска');

var all = { p: true, m: true, v: true };
// A day-long solve is clipped: the axis follows the medians, not the outlier.
var releases = [
  { m: { time: 300 }, p: { time: 250 }, v: null },
  { m: { time: 400 }, p: { time: 86400 }, v: null },
];
var domain = stats.yDomain(releases, 'time', all);
assert.ok(domain.top >= 1200 && domain.top <= 1800, 'time axis top ' + domain.top);
assert.strictEqual(domain.cap, domain.top);
// Nothing to clip: the axis just fits the data.
var calm = stats.yDomain([{ m: { time: 300 }, p: { time: 420 }, v: null }], 'time', all);
assert.strictEqual(calm.cap, null);
assert.ok(calm.top >= 420);
// Hidden series do not stretch the axis.
var hidden = stats.yDomain(releases, 'time', { p: false, m: true, v: true });
assert.strictEqual(hidden.cap, null);
assert.ok(hidden.top < 1200);
// Score is always 0..100.
assert.deepStrictEqual(stats.yDomain(releases, 'score', all), { top: 100, step: 25, cap: null });

console.log('player_game_stats tests passed');
