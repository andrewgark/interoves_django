'use strict';

var assert = require('assert');

function loadHelperWithWindow(fakeWindow) {
  global.window = fakeWindow;
  global.interovesAnalytics = undefined;
  delete require.cache[require.resolve('./yandex_metrika_helper.js')];
  return require('./yandex_metrika_helper.js');
}

(function testSafeWithoutYm() {
  var helper = loadHelperWithWindow({ interovesAnalyticsConfig: { yandexCounterId: 108320022 } });
  assert.strictEqual(helper.trackYandexGoal('game_start', { game: 'ladder' }), false);
})();

(function testGoalAndParamsForwarded() {
  var calls = [];
  var helper = loadHelperWithWindow({
    interovesAnalyticsConfig: { yandexCounterId: 108320022 },
    ym: function () { calls.push([].slice.call(arguments)); },
  });
  assert.strictEqual(helper.trackYandexGoal('game_complete', { game: 'alphabet', game_id: '10' }), true);
  assert.deepStrictEqual(calls[0], [
    108320022,
    'reachGoal',
    'game_complete',
    { game: 'alphabet', game_id: '10' },
  ]);
})();

(function testOnceDedupe() {
  var calls = [];
  var helper = loadHelperWithWindow({
    interovesAnalyticsConfig: { yandexCounterId: 108320022 },
    ym: function () { calls.push([].slice.call(arguments)); },
  });
  helper.trackYandexGoalOnce('same-key', 'game_start', { game: 'replacement' });
  helper.trackYandexGoalOnce('same-key', 'game_start', { game: 'replacement' });
  assert.strictEqual(calls.length, 1);
})();

(function testQueuedGoalFlushesAfterYmAppears() {
  var calls = [];
  var timers = [];
  var fakeWindow = {
    interovesAnalyticsConfig: { yandexCounterId: 108320022 },
    setTimeout: function (fn) {
      timers.push(fn);
      return timers.length;
    },
    addEventListener: function () {},
  };
  var helper = loadHelperWithWindow(fakeWindow);
  assert.strictEqual(helper.trackYandexGoalOnce('late-key', 'game_start', { game: 'ladder' }), false);
  fakeWindow.ym = function () { calls.push([].slice.call(arguments)); };
  while (timers.length) timers.shift()();
  assert.strictEqual(calls.length, 1);
  assert.deepStrictEqual(calls[0], [
    108320022,
    'reachGoal',
    'game_start',
    { game: 'ladder' },
  ]);
})();

console.log('yandex_metrika_helper.test.js: ok');
