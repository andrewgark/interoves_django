'use strict';

var assert = require('assert');
require('./raddle_keyboard_anchor.js');
var A = global.RaddleKeyboardAnchor;

function testPairAlign() {
  assert.strictEqual(A.pairAlign('next'), 'end');
  assert.strictEqual(A.pairAlign('prev'), 'start');
  assert.strictEqual(A.pairAlign(''), 'nearest');
  assert.strictEqual(A.pairAlign(null), 'nearest');
}

function testFocusedInputIsVisible() {
  var vv = { offsetTop: 0, height: 400 };
  assert.strictEqual(A.focusedInputIsVisible({ top: 80, bottom: 128 }, vv, 56), true);
  assert.strictEqual(A.focusedInputIsVisible({ top: -40, bottom: 8 }, vv, 56), false);
  assert.strictEqual(A.focusedInputIsVisible({ top: 380, bottom: 428 }, vv, 56), false);
}

function testStickyPinHiddenWhenRowVisibleBelowNav() {
  assert.strictEqual(A.shouldShowStickyPin({
    firstTop: 56,
    firstBottom: 104,
    viewportHeight: 400,
    taskBottom: 800,
    navTop: 56,
    pinH: 96,
  }), false);
}

function testStickyPinHiddenWhenKeyboardKeepsRowAtTop() {
  assert.strictEqual(A.shouldShowStickyPin({
    firstTop: 60,
    firstBottom: 108,
    viewportHeight: 360,
    taskBottom: 800,
    navTop: 56,
    pinH: 96,
  }), false);
}

function testStickyPinShowsWhenPairScrolledUnderNav() {
  assert.strictEqual(A.shouldShowStickyPin({
    firstTop: -40,
    firstBottom: 8,
    viewportHeight: 400,
    taskBottom: 800,
    navTop: 56,
    pinH: 96,
  }), true);
}

function testStickyPinKeepsFocusWhileKeyboardCoversTaskBottom() {
  assert.strictEqual(A.shouldShowStickyPin({
    firstTop: -40,
    firstBottom: 8,
    viewportHeight: 400,
    taskBottom: 40,
    navTop: 56,
    pinH: 96,
    pinHasFocus: true,
  }), true);
}

function testStickyPinStaysWhenTypingInPinEvenIfPairLooksVisible() {
  assert.strictEqual(A.shouldShowStickyPin({
    firstTop: 60,
    firstBottom: 108,
    viewportHeight: 360,
    taskBottom: 800,
    navTop: 56,
    pinH: 96,
    pinHasFocus: true,
  }), true);
}

function testStickyPinStaysWhileTypingIfRealFieldOffscreen() {
  assert.strictEqual(A.shouldShowStickyPin({
    firstTop: 60,
    firstBottom: 108,
    viewportHeight: 360,
    taskBottom: 800,
    navTop: 56,
    pinH: 96,
    keepIfActive: true,
  }), true);
}

function testScrollDeltaPinsBottomPairToVisualBottom() {
  var delta = A.scrollDeltaForPair({
    pairTop: 80,
    pairBottom: 176,
    viewportOffsetTop: 0,
    viewportHeight: 700,
    navTop: 56,
    align: 'end',
    margin: 8,
  });
  assert.strictEqual(delta, 176 - (700 - 8));
}

function testScrollDeltaPinsTopPairUnderNav() {
  var delta = A.scrollDeltaForPair({
    pairTop: 400,
    pairBottom: 496,
    viewportOffsetTop: 0,
    viewportHeight: 700,
    navTop: 56,
    align: 'start',
    margin: 8,
  });
  assert.strictEqual(delta, 400 - (56 + 8));
}

function testScrollDeltaNearestLeavesFullyVisiblePair() {
  var delta = A.scrollDeltaForPair({
    pairTop: 200,
    pairBottom: 296,
    viewportOffsetTop: 0,
    viewportHeight: 700,
    navTop: 56,
    align: 'nearest',
    margin: 8,
  });
  assert.strictEqual(delta, 0);
}

function testKeyboardCloseScrollsVisibleJumpedPair() {
  assert.strictEqual(A.shouldScrollPairOnKeyboardClose({
    pairTop: 80,
    pairBottom: 176,
    viewportOffsetTop: 0,
    viewportHeight: 700,
    navTop: 56,
  }), true);
}

function testKeyboardCloseDoesNotYankPairAfterScrollToClues() {
  assert.strictEqual(A.shouldScrollPairOnKeyboardClose({
    pairTop: -200,
    pairBottom: -80,
    viewportOffsetTop: 0,
    viewportHeight: 700,
    navTop: 56,
  }), false);
}

function testKeyboardCloseDoesNotYankPairBelowFold() {
  assert.strictEqual(A.shouldScrollPairOnKeyboardClose({
    pairTop: 800,
    pairBottom: 896,
    viewportOffsetTop: 0,
    viewportHeight: 700,
    navTop: 56,
  }), false);
}

function testDismissRetargetFromEmptySpace() {
  var input = { contains: function () { return false; } };
  var row = { contains: function () { return false; } };
  var empty = { closest: function () { return null; } };
  assert.strictEqual(A.isDismissRetarget({
    pointerDownTarget: empty,
    focusInput: input,
    focusRow: row,
  }), true);
}

function testDismissRetargetAllowsExplicitRowTap() {
  var input = { contains: function () { return false; } };
  var row;
  var down = {
    closest: function (sel) {
      if (sel === '.new-raddle-row') return row;
      return null;
    },
  };
  row = {
    contains: function (el) { return el === down; },
  };
  assert.strictEqual(A.isDismissRetarget({
    pointerDownTarget: down,
    focusInput: input,
    focusRow: row,
  }), false);
}

function testDismissRetargetAllowsSwitchingToAnotherWord() {
  var input = { contains: function () { return false; } };
  var focusRow = { contains: function () { return false; } };
  var otherRow = {};
  var down = {
    closest: function (sel) {
      if (sel === '.new-raddle-row') return otherRow;
      return null;
    },
  };
  assert.strictEqual(A.isDismissRetarget({
    pointerDownTarget: down,
    focusInput: input,
    focusRow: focusRow,
  }), false);
}

function testDismissRetargetIgnoresMissingPointerDown() {
  var input = { contains: function () { return false; } };
  assert.strictEqual(A.isDismissRetarget({
    pointerDownTarget: null,
    focusInput: input,
  }), false);
}

testPairAlign();
testFocusedInputIsVisible();
testStickyPinHiddenWhenRowVisibleBelowNav();
testStickyPinHiddenWhenKeyboardKeepsRowAtTop();
testStickyPinShowsWhenPairScrolledUnderNav();
testStickyPinKeepsFocusWhileKeyboardCoversTaskBottom();
testStickyPinStaysWhenTypingInPinEvenIfPairLooksVisible();
testStickyPinStaysWhileTypingIfRealFieldOffscreen();
testScrollDeltaPinsBottomPairToVisualBottom();
testScrollDeltaPinsTopPairUnderNav();
testScrollDeltaNearestLeavesFullyVisiblePair();
testKeyboardCloseScrollsVisibleJumpedPair();
testKeyboardCloseDoesNotYankPairAfterScrollToClues();
testKeyboardCloseDoesNotYankPairBelowFold();
testDismissRetargetFromEmptySpace();
testDismissRetargetAllowsExplicitRowTap();
testDismissRetargetAllowsSwitchingToAnotherWord();
testDismissRetargetIgnoresMissingPointerDown();
console.log('raddle_keyboard_anchor.test.js: ok');
