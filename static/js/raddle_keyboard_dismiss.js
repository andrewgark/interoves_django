/**
 * Mobile raddle: keep caret on the current pair when the keyboard closes, and
 * decide when the sticky pin should be the actual input (pair under the nav).
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

  function numeric(value, fallback) {
    var n = Number(value);
    return isFinite(n) ? n : fallback;
  }

  /**
   * Show the sticky pin when the highlighted pair has gone under the nav in
   * the visual viewport. If the user is already typing in the pin, keep it
   * even when the keyboard pans the real pair back into the leftover view
   * (hiding it would jump the page). Never keep it when the pair has gone
   * *below* the viewport — that is looking at words above the current step.
   */
  function shouldShowStickyPin(opts) {
    opts = opts || {};
    var offset = numeric(opts.viewportOffsetTop, 0) || 0;
    var navTop = numeric(opts.navTop, 0) || 0;
    var pinH = numeric(opts.pinH, 0) || 0;
    var vh = Number(opts.viewportHeight);
    var firstTopRaw = Number(opts.firstTop);
    var firstBottomRaw = Number(opts.firstBottom);
    if (!isFinite(firstBottomRaw) && isFinite(firstTopRaw)) firstBottomRaw = firstTopRaw + 48;
    var firstTop = firstTopRaw - offset;
    var firstBottom = firstBottomRaw - offset;
    var taskBottom = Number(opts.taskBottom) - offset;
    if (!isFinite(firstTop) || !isFinite(firstBottom)) return false;
    var rowH = Math.max(0, firstBottom - firstTop);
    var sliver = Math.min(24, Math.max(8, rowH * 0.5));
    var taskStillVisible = isFinite(taskBottom) &&
      taskBottom > navTop + Math.min(pinH || 48, 24);
    if (!taskStillVisible) return false;
    var pairBelowView = isFinite(vh) && firstTop >= vh - sliver;
    if (pairBelowView) return false;
    if (opts.pinHasFocus) return true;
    var underNav = (firstBottom - navTop) < sliver;
    return underNav;
  }

  global.RaddleKeyboardDismiss = {
    isDismissRetarget: isDismissRetarget,
    isStolenFocus: isStolenFocus,
    shouldBlockFocusMove: shouldBlockFocusMove,
    shouldShowStickyPin: shouldShowStickyPin,
  };
})(typeof window !== 'undefined' ? window : globalThis);
