'use strict';

const assert = require('assert');

const values = Object.create(null);
const listeners = Object.create(null);
const tracked = [];

function addListener(type, callback) {
  (listeners[type] = listeners[type] || []).push(callback);
}

function dispatch(event) {
  (listeners[event.type] || []).forEach(function (callback) { callback(event); });
}

function fakeLink(attributes) {
  const callbacks = Object.create(null);
  return {
    getAttribute: function (name) { return attributes[name] || null; },
    addEventListener: function (type, callback) { callbacks[type] = callback; },
    click: function () { callbacks.click(); }
  };
}

const startLink = fakeLink({
  'data-onboarding-game': 'salad',
  'data-onboarding-recommended': '1'
});

global.window = {
  localStorage: {
    getItem: function (key) { return values[key] || null; },
    setItem: function (key, value) { values[key] = String(value); },
    removeItem: function (key) { delete values[key]; }
  },
  document: {
    querySelectorAll: function () { return [startLink]; }
  },
  interovesAnalytics: {
    trackYandexGoalOnce: function (key, goal, params) {
      tracked.push({ key: key, goal: goal, params: params });
      return true;
    }
  },
  addEventListener: addListener,
  dispatchEvent: dispatch
};

require('./onboarding.js');

const onboarding = global.window.interovesOnboarding;
onboarding.initStartPage();
startLink.click();

let context = onboarding._readContext();
assert.strictEqual(context.stage, 'selected');
assert.strictEqual(context.selectedGame, 'salad');
assert.strictEqual(context.recommended, true);
assert.deepStrictEqual(
  tracked.slice(0, 2).map(function (item) { return item.goal; }),
  ['onboarding_view', 'onboarding_game_select']
);
assert.deepStrictEqual(tracked[1].params, { game: 'salad', recommended: true });

const nextLink = fakeLink({ 'data-onboarding-next-game': 'alphabetty' });
const archiveLink = fakeLink({ 'data-onboarding-next-game': 'salad' });
const firstBlock = {
  hidden: true,
  getAttribute: function () { return 'salad'; },
  querySelectorAll: function () { return [nextLink, archiveLink]; }
};
onboarding.initGamePage(firstBlock);

dispatch({
  type: 'interoves:analytics-goals',
  detail: { goals: [
    { goal: 'game_start', params: { game: 'salad', game_id: '1' } },
    { goal: 'game_complete', params: { game: 'salad', game_id: '1' } }
  ] }
});

context = onboarding._readContext();
assert.strictEqual(context.stage, 'completed');
assert.strictEqual(firstBlock.hidden, false);
assert.strictEqual(tracked[2].goal, 'onboarding_first_game_complete');

archiveLink.click();
assert.strictEqual(onboarding._readContext().stage, 'awaiting_second_start');
assert.strictEqual(onboarding._readContext().targetGame, 'salad');

// Return to the completed state: an actual second start also counts when the
// player used ordinary navigation instead of a follow-up CTA.
context.stage = 'completed';
window.localStorage.setItem('interoves_onboarding_v2', JSON.stringify(context));
dispatch({
  type: 'interoves:analytics-goals',
  detail: { goals: [
    { goal: 'game_start', params: { game: 'salad', game_id: '1' } }
  ] }
});
assert.strictEqual(tracked.length, 3);

const secondBlock = {
  hidden: true,
  getAttribute: function () { return 'alphabetty'; },
  querySelectorAll: function () { return []; }
};
onboarding.initGamePage(secondBlock);
dispatch({
  type: 'interoves:analytics-goals',
  detail: { goals: [
    { goal: 'game_start', params: { game: 'alphabet', game_id: '2' } }
  ] }
});

assert.strictEqual(onboarding._readContext().stage, 'second_started');
assert.strictEqual(tracked[3].goal, 'onboarding_second_game_start');
assert.deepStrictEqual(tracked[3].params, { first_game: 'salad', game: 'alphabetty' });

delete global.window;
console.log('onboarding.js tests passed');
