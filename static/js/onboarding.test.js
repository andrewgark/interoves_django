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
const scheduled = [];

function fakeSocialPrompt() {
  const attrs = Object.create(null);
  const closeCallbacks = Object.create(null);
  const telegram = fakeLink({ 'data-onboarding-social-platform': 'telegram' });
  const instagram = fakeLink({ 'data-onboarding-social-platform': 'instagram' });
  const twitter = fakeLink({ 'data-onboarding-social-platform': 'twitter' });
  const closeBtn = {
    addEventListener: function (type, callback) { closeCallbacks[type] = callback; },
    click: function () { closeCallbacks.click(); }
  };
  return {
    hidden: true,
    telegram: telegram,
    instagram: instagram,
    twitter: twitter,
    closeBtn: closeBtn,
    getAttribute: function (name) { return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null; },
    setAttribute: function (name, value) { attrs[name] = String(value); },
    querySelector: function (selector) {
      return selector === '[data-onboarding-social-dismiss]' ? closeBtn : null;
    },
    querySelectorAll: function (selector) {
      return selector === '[data-onboarding-social-platform]'
        ? [telegram, instagram, twitter]
        : [];
    }
  };
}

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
  setTimeout: function (fn, ms) {
    scheduled.push({ fn: fn, ms: ms });
    return scheduled.length;
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
const socialPrompt = fakeSocialPrompt();
const firstBlock = {
  hidden: true,
  getAttribute: function () { return 'salad'; },
  querySelector: function (selector) {
    return selector === '[data-onboarding-social-prompt]' ? socialPrompt : null;
  },
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
assert.strictEqual(socialPrompt.hidden, true);
assert.strictEqual(scheduled.length, 1);
assert.strictEqual(scheduled[0].ms, 1600);

scheduled[0].fn();
context = onboarding._readContext();
assert.strictEqual(socialPrompt.hidden, false);
assert.strictEqual(context.socialFollowPromptShown, true);
assert.strictEqual(tracked[3].goal, 'social_follow_prompt_view');
assert.deepStrictEqual(tracked[3].params, { game: 'salad' });

socialPrompt.telegram.click();
assert.strictEqual(socialPrompt.hidden, true);
assert.strictEqual(tracked[4].goal, 'social_follow_click');
assert.deepStrictEqual(tracked[4].params, { platform: 'telegram' });
socialPrompt.instagram.click();
assert.deepStrictEqual(tracked[5].params, { platform: 'instagram' });
socialPrompt.twitter.click();
assert.deepStrictEqual(tracked[6].params, { platform: 'twitter' });

socialPrompt.hidden = false;
socialPrompt.closeBtn.click();
assert.strictEqual(socialPrompt.hidden, true);
assert.strictEqual(onboarding._readContext().socialFollowPromptDismissed, true);
assert.strictEqual(tracked[7].goal, 'social_follow_prompt_dismiss');

dispatch({
  type: 'interoves:analytics-goals',
  detail: { goals: [
    { goal: 'game_complete', params: { game: 'salad', game_id: '1' } }
  ] }
});
assert.strictEqual(tracked.length, 8);
assert.strictEqual(socialPrompt.hidden, true);

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
assert.strictEqual(tracked.length, 8);

const secondBlock = {
  hidden: true,
  getAttribute: function () { return 'alphabetty'; },
  querySelector: function () { return null; },
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
assert.strictEqual(tracked[8].goal, 'onboarding_second_game_start');
assert.deepStrictEqual(tracked[8].params, { first_game: 'salad', game: 'alphabetty' });

delete global.window;
console.log('onboarding.js tests passed');
