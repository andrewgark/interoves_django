'use strict';

var assert = require('assert');
require('./raddle_keyboard_dismiss.js');
var D = global.RaddleKeyboardDismiss;

function emptyEl() {
  return { closest: function () { return null; }, contains: function () { return false; } };
}

function testDismissRetargetFromEmptySpace() {
  var input = { contains: function () { return false; } };
  var row = { contains: function () { return false; } };
  assert.strictEqual(D.isDismissRetarget({
    pointerDownTarget: emptyEl(),
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
  assert.strictEqual(D.isDismissRetarget({
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
  assert.strictEqual(D.isDismissRetarget({
    pointerDownTarget: down,
    focusInput: input,
    focusRow: focusRow,
  }), false);
}

function testDismissRetargetIgnoresMissingPointerDown() {
  var input = { contains: function () { return false; } };
  assert.strictEqual(D.isDismissRetarget({
    pointerDownTarget: null,
    focusInput: input,
  }), false);
}

function testStolenFocusDetectsOtherWord() {
  assert.strictEqual(D.isStolenFocus({
    anchor: { taskId: '7', wordIndex: '4' },
    taskId: '7',
    wordIndex: '1',
  }), true);
  assert.strictEqual(D.isStolenFocus({
    anchor: { taskId: '7', wordIndex: '4' },
    taskId: '7',
    wordIndex: '4',
  }), false);
}

function testBlockGhostClickOntoWordAbove() {
  assert.strictEqual(D.shouldBlockFocusMove({
    pointerDownTarget: emptyEl(),
    focusInput: { contains: function () { return false; } },
    focusRow: { contains: function () { return false; } },
    anchor: { taskId: '7', wordIndex: '4' },
    taskId: '7',
    wordIndex: '1',
  }), true);
}

function testBlockBackButtonFocusSteal() {
  assert.strictEqual(D.shouldBlockFocusMove({
    pointerDownTarget: null,
    focusInput: { contains: function () { return false; } },
    focusRow: { contains: function () { return false; } },
    keyboardClosedRecently: true,
    anchor: { taskId: '7', wordIndex: '4' },
    taskId: '7',
    wordIndex: '1',
  }), true);
}

function testAllowTapAnotherWordRightAfterKeyboardClose() {
  var focusRow;
  var down = {
    closest: function (sel) {
      if (sel === '.new-raddle-row') return focusRow;
      return null;
    },
  };
  focusRow = { contains: function (el) { return el === down; } };
  assert.strictEqual(D.shouldBlockFocusMove({
    pointerDownTarget: down,
    focusInput: { contains: function () { return false; } },
    focusRow: focusRow,
    keyboardClosedRecently: true,
    anchor: { taskId: '7', wordIndex: '4' },
    taskId: '7',
    wordIndex: '1',
  }), false);
}

function testAllowProgrammaticAdvanceWithoutPointerDown() {
  assert.strictEqual(D.shouldBlockFocusMove({
    pointerDownTarget: null,
    focusInput: { contains: function () { return false; } },
    focusRow: { contains: function () { return false; } },
    keyboardClosedRecently: false,
    anchor: { taskId: '7', wordIndex: '4' },
    taskId: '7',
    wordIndex: '3',
  }), false);
}

function testAllowSameWordAfterKeyboardClose() {
  assert.strictEqual(D.shouldBlockFocusMove({
    pointerDownTarget: null,
    focusInput: { contains: function () { return false; } },
    focusRow: { contains: function () { return false; } },
    keyboardClosedRecently: true,
    anchor: { taskId: '7', wordIndex: '4' },
    taskId: '7',
    wordIndex: '4',
  }), false);
}

function testBlockBackStealBeforeViewportReportsClose() {
  assert.strictEqual(D.shouldBlockFocusMove({
    pointerDownTarget: null,
    focusInput: { contains: function () { return false; } },
    focusRow: { contains: function () { return false; } },
    dismissSessionActive: true,
    keyboardClosedRecently: false,
    anchor: { taskId: '7', wordIndex: '4' },
    taskId: '7',
    wordIndex: '1',
  }), true);
}

function testBlockBackStealWithStalePointerOnOriginalWord() {
  var originalRow = {};
  var stolenRow = { contains: function () { return false; } };
  var down = {
    closest: function (sel) {
      if (sel === '.new-raddle-row') return originalRow;
      return null;
    },
  };
  assert.strictEqual(D.shouldBlockFocusMove({
    pointerDownTarget: down,
    focusInput: { contains: function () { return false; } },
    focusRow: stolenRow,
    dismissSessionActive: true,
    anchor: { taskId: '7', wordIndex: '4' },
    taskId: '7',
    wordIndex: '1',
  }), true);
}

function testAllowAdvanceWhenAnchorAlreadyMoved() {
  assert.strictEqual(D.shouldBlockFocusMove({
    pointerDownTarget: null,
    focusInput: { contains: function () { return false; } },
    focusRow: { contains: function () { return false; } },
    dismissSessionActive: true,
    keyboardClosedRecently: true,
    anchor: { taskId: '7', wordIndex: '3' },
    taskId: '7',
    wordIndex: '3',
  }), false);
}

function testStickyPinHiddenWhenPairVisibleBelowNav() {
  assert.strictEqual(D.shouldShowStickyPin({
    firstTop: 80,
    firstBottom: 128,
    taskBottom: 800,
    navTop: 56,
    pinH: 96,
    viewportOffsetTop: 0,
  }), false);
}

function testStickyPinShowsWhenPairScrolledUnderNav() {
  assert.strictEqual(D.shouldShowStickyPin({
    firstTop: -40,
    firstBottom: 8,
    taskBottom: 800,
    navTop: 56,
    pinH: 96,
    viewportOffsetTop: 0,
  }), true);
}

function testStickyPinHiddenWhileKeyboardKeepsPairInView() {
  // Layout top 400, keyboard pan 300 → pair still sits below the nav in view.
  assert.strictEqual(D.shouldShowStickyPin({
    firstTop: 400,
    firstBottom: 448,
    taskBottom: 900,
    navTop: 56,
    pinH: 96,
    viewportOffsetTop: 300,
  }), false);
}

function testStickyPinShowsWhenVisualViewportPansPastPair() {
  // User scrolled the visual viewport down; the pair is above what they see.
  assert.strictEqual(D.shouldShowStickyPin({
    firstTop: 200,
    firstBottom: 248,
    taskBottom: 900,
    navTop: 56,
    pinH: 96,
    viewportOffsetTop: 400,
  }), true);
}

function testStickyPinHiddenWhenPairIsBelowTheViewport() {
  assert.strictEqual(D.shouldShowStickyPin({
    firstTop: 700,
    firstBottom: 748,
    taskBottom: 900,
    navTop: 56,
    pinH: 96,
    viewportOffsetTop: 0,
    viewportHeight: 360,
  }), false);
}

function testStickyPinStaysWhileTypingIfKeyboardPansRealPair() {
  assert.strictEqual(D.shouldShowStickyPin({
    firstTop: 400,
    firstBottom: 448,
    taskBottom: 900,
    navTop: 56,
    pinH: 96,
    viewportOffsetTop: 300,
    viewportHeight: 360,
    pinHasFocus: true,
  }), true);
}

function testStickyPinHidesWhenLookingAtWordsAboveEvenIfPinHadFocus() {
  assert.strictEqual(D.shouldShowStickyPin({
    firstTop: 700,
    firstBottom: 748,
    taskBottom: 900,
    navTop: 56,
    pinH: 96,
    viewportOffsetTop: 0,
    viewportHeight: 360,
    pinHasFocus: true,
  }), false);
}

testDismissRetargetFromEmptySpace();
testDismissRetargetAllowsExplicitRowTap();
testDismissRetargetAllowsSwitchingToAnotherWord();
testDismissRetargetIgnoresMissingPointerDown();
testStolenFocusDetectsOtherWord();
testBlockGhostClickOntoWordAbove();
testBlockBackButtonFocusSteal();
testAllowTapAnotherWordRightAfterKeyboardClose();
testAllowProgrammaticAdvanceWithoutPointerDown();
testAllowSameWordAfterKeyboardClose();
testBlockBackStealBeforeViewportReportsClose();
testBlockBackStealWithStalePointerOnOriginalWord();
testAllowAdvanceWhenAnchorAlreadyMoved();
testStickyPinHiddenWhenPairVisibleBelowNav();
testStickyPinShowsWhenPairScrolledUnderNav();
testStickyPinHiddenWhileKeyboardKeepsPairInView();
testStickyPinShowsWhenVisualViewportPansPastPair();
testStickyPinHiddenWhenPairIsBelowTheViewport();
testStickyPinStaysWhileTypingIfKeyboardPansRealPair();
testStickyPinHidesWhenLookingAtWordsAboveEvenIfPinHadFocus();
console.log('raddle_keyboard_dismiss.test.js: ok');
