'use strict';

var assert = require('assert');
require('./stuck_nudge.js');
var nudge = global.InterovesStuckNudge;

var options = {glowAfter: {ms: 60000, misses: 2}, calloutAfter: {ms: 120000, misses: 4}};
function state(extra) {
  var base = {
    now: 100000, lastInputAt: 0, activeMs: 0, misses: 0, glowShown: false,
    calloutSeen: false, hinted: false, off: false, done: false,
  };
  Object.keys(extra || {}).forEach(function (k) { base[k] = extra[k]; });
  return base;
}

assert.strictEqual(nudge.dueStage(state(), options), '');
assert.strictEqual(nudge.dueStage(state({activeMs: 60000}), options), 'glow');
assert.strictEqual(nudge.dueStage(state({misses: 2}), options), 'glow');
assert.strictEqual(nudge.dueStage(state({activeMs: 60000, glowShown: true}), options), '');
assert.strictEqual(nudge.dueStage(state({activeMs: 120000, glowShown: true}), options), 'callout');
assert.strictEqual(nudge.dueStage(state({misses: 4}), options), 'callout');
// Typing right now: wait for a pause.
assert.strictEqual(nudge.dueStage(state({activeMs: 120000, lastInputAt: 100000 - nudge.QUIET_MS + 1}), options), '');
// Callout already seen for this game, or a hint already taken: glow only.
assert.strictEqual(nudge.dueStage(state({activeMs: 120000, calloutSeen: true}), options), 'glow');
assert.strictEqual(nudge.dueStage(state({activeMs: 120000, hinted: true}), options), 'glow');
assert.strictEqual(nudge.dueStage(state({activeMs: 120000, off: true}), options), '');
assert.strictEqual(nudge.dueStage(state({activeMs: 120000, done: true}), options), '');
// No miss thresholds (salad): only time counts.
assert.strictEqual(nudge.dueStage(state({misses: 50}), {glowAfter: {ms: 90000}, calloutAfter: {ms: 180000}}), '');

var viewport = {width: 400, height: 800};
var box = {width: 200, height: 80};
var spot = nudge.place({top: 300, bottom: 330, left: 180, width: 30}, box, viewport);
assert.strictEqual(spot.side, 'above');
assert.strictEqual(spot.top, 300 - 80 - 10);
assert.strictEqual(spot.left, 95);
assert.strictEqual(spot.arrow, 100);
// No room above: below the button.
assert.strictEqual(nudge.place({top: 40, bottom: 70, left: 180, width: 30}, box, viewport).side, 'below');
// Near the right edge: clamped; the arrow stays off the rounded corner.
spot = nudge.place({top: 300, bottom: 330, left: 360, width: 30}, box, viewport);
assert.strictEqual(spot.left, 188);
assert.strictEqual(spot.arrow, 200 - 16);

console.log('stuck_nudge.test.js: ok');
