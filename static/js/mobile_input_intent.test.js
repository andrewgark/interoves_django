'use strict';

var assert = require('assert');
require('./mobile_input_intent.js');
var Intent = global.MobileInputIntent;

function testContextAndExplicitClear() {
  var state = Intent.create();
  assert.strictEqual(state.isActive('7'), false);
  state.activate(7);
  assert.strictEqual(state.isActive('7'), true);
  assert.strictEqual(state.isActive('8'), false);
  state.clear();
  assert.strictEqual(state.isActive(), false);
}

function testKeyboardCloseClearsStaleFocusIntent() {
  var state = Intent.create({ keyboardCloseDelta: 80 });
  state.observeViewport(390, 420);
  state.activate('task-1');
  assert.strictEqual(state.observeViewport(390, 650), true);
  assert.strictEqual(state.isActive('task-1'), false);
}

function testKeyboardOpeningDoesNotClearIntent() {
  var state = Intent.create({ keyboardCloseDelta: 80 });
  state.observeViewport(390, 650);
  state.activate('task-1');
  assert.strictEqual(state.observeViewport(390, 420), false);
  assert.strictEqual(state.isActive('task-1'), true);
}

function testAnimatedKeyboardCloseUsesSmallestObservedHeight() {
  var state = Intent.create({ keyboardCloseDelta: 120 });
  state.observeViewport(390, 650);
  state.activate('task-1');
  state.observeViewport(390, 400);
  assert.strictEqual(state.observeViewport(390, 470), false);
  assert.strictEqual(state.observeViewport(390, 540), true);
  assert.strictEqual(state.isActive('task-1'), false);
}

function testBrowserToolbarMovementIsIgnored() {
  var state = Intent.create({ keyboardCloseDelta: 80 });
  state.observeViewport(390, 600);
  state.activate('task-1');
  assert.strictEqual(state.observeViewport(390, 650), false);
  assert.strictEqual(state.isActive('task-1'), true);
}

function testOrientationChangeClearsIntentWithoutReportingKeyboardClose() {
  var state = Intent.create({ viewportWidthResetDelta: 40 });
  state.observeViewport(390, 650);
  state.activate('task-1');
  assert.strictEqual(state.observeViewport(700, 350), false);
  assert.strictEqual(state.isActive('task-1'), false);
}

function testBoundedFocusWindowExpires() {
  var now = 1000;
  var state = Intent.create({ durationMs: 700, now: function () { return now; } });
  state.activate('alphabetty');
  now = 1700;
  assert.strictEqual(state.isActive('alphabetty'), true);
  now = 1701;
  assert.strictEqual(state.isActive('alphabetty'), false);
}

testContextAndExplicitClear();
testKeyboardCloseClearsStaleFocusIntent();
testKeyboardOpeningDoesNotClearIntent();
testAnimatedKeyboardCloseUsesSmallestObservedHeight();
testBrowserToolbarMovementIsIgnored();
testOrientationChangeClearsIntentWithoutReportingKeyboardClose();
testBoundedFocusWindowExpires();
console.log('mobile_input_intent.test.js: ok');
