/**
 * Mobile keyboard dismiss: keep the raddle pair you were typing on.
 * Tap-empty can land on another input after the page jumps; Back can let the
 * browser focus a still-visible word above. Sticky-pin visibility is unchanged.
 *
 * Local check: node static/js/raddle_keyboard_dismiss.test.js
 */
(function (global) {
  'use strict';

  function closestFrom(el, selector) {
    if (!el) return null;
    if (el.closest) return el.closest(selector);
    return null;
  }

  function pointerOnFocusTarget(opts) {
    var down = opts.pointerDownTarget;
    var input = opts.focusInput;
    var row = opts.focusRow;
    if (!down) return false;
    if (input && (down === input || (input.contains && input.contains(down)))) return true;
    if (row && row.contains && row.contains(down)) return true;
    var downRow = closestFrom(down, '.new-raddle-row');
    if (downRow && row && downRow === row) return true;
    var downInput = closestFrom(down, 'input.new-raddle-input');
    if (downInput && input && downInput === input) return true;
    return false;
  }

  function isDismissRetarget(opts) {
    opts = opts || {};
    var down = opts.pointerDownTarget;
    var input = opts.focusInput;
    if (!down || !input) return false;
    if (pointerOnFocusTarget(opts)) return false;
    var row = opts.focusRow || closestFrom(input, '.new-raddle-row');
    var downRow = closestFrom(down, '.new-raddle-row');
    if (downRow && downRow !== row) return false;
    var downInput = closestFrom(down, 'input.new-raddle-input');
    if (downInput && downInput !== input) return false;
    if (closestFrom(down, '.new-raddle-sticky-pin')) return false;
    return true;
  }

  function isStolenFocus(opts) {
    opts = opts || {};
    var anchor = opts.anchor;
    if (!anchor || opts.taskId == null || opts.wordIndex == null) return false;
    if (String(anchor.taskId) !== String(opts.taskId)) return false;
    return String(anchor.wordIndex) !== String(opts.wordIndex);
  }

  function shouldBlockFocusMove(opts) {
    opts = opts || {};
    // A tap on this row/input is always a real switch, even during dismiss.
    if (pointerOnFocusTarget(opts)) return false;
    if (isDismissRetarget(opts)) return true;
    if (!isStolenFocus(opts)) return false;
    // Back can steal focus before visualViewport reports the close, and the
    // last pointerdown is often still the original word. Treat that as steal
    // while the dismiss session is open or just finished.
    return !!(opts.keyboardClosedRecently || opts.dismissSessionActive);
  }

  global.RaddleKeyboardDismiss = {
    isDismissRetarget: isDismissRetarget,
    isStolenFocus: isStolenFocus,
    shouldBlockFocusMove: shouldBlockFocusMove,
  };
})(typeof window !== 'undefined' ? window : globalThis);
