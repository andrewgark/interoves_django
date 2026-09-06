/**
 * Mobile keyboard close: keep the raddle pair you were typing on, instead of
 * letting Chrome/Safari retarget focus to another input or leave the caret
 * next to the word above.
 *
 * Local check: node static/js/raddle_keyboard_anchor.test.js
 */
(function (global) {
  'use strict';

  function visualOffsetTop(viewport) {
    return viewport && typeof viewport.offsetTop === 'number' ? viewport.offsetTop : 0;
  }

  function visualHeight(viewport, fallback) {
    if (viewport && typeof viewport.height === 'number') return viewport.height;
    return fallback || 0;
  }

  function focusedInputIsVisible(rect, viewport, navTop) {
    if (!rect) return false;
    var offset = visualOffsetTop(viewport);
    var vh = visualHeight(viewport, 0);
    var top = rect.top - offset;
    var bottom = rect.bottom - offset;
    var visibleTop = Math.max(top, navTop || 0);
    var visibleBottom = Math.min(bottom, vh);
    var minVisible = Math.min(24, Math.max(0, rect.bottom - rect.top));
    return visibleBottom - visibleTop >= minVisible && minVisible > 0;
  }

  function shouldShowStickyPin(opts) {
    opts = opts || {};
    var navTop = opts.navTop || 0;
    var pinH = opts.pinH || 0;
    var firstTop = Number(opts.firstTop);
    var firstBottom = Number(opts.firstBottom);
    if (!isFinite(firstBottom) && isFinite(firstTop)) firstBottom = firstTop + 48;
    var vh = opts.viewportHeight;
    if (vh == null || !isFinite(Number(vh))) vh = Infinity;
    else vh = Number(vh);
    var visibleTop = Math.max(firstTop, navTop);
    var visibleBottom = Math.min(firstBottom, vh);
    var visiblePx = visibleBottom - visibleTop;
    var rowH = Math.max(0, firstBottom - firstTop);
    var minVisible = Math.min(24, Math.max(8, rowH * 0.5));
    var pairHidden = !isFinite(firstTop) || visiblePx < minVisible;
    var taskStillVisible = opts.taskBottom > navTop + Math.min(pinH, 24);
    return pairHidden && (taskStillVisible || !!opts.pinHasFocus);
  }

  function pairAlign(refRole) {
    if (refRole === 'next') return 'end';
    if (refRole === 'prev') return 'start';
    return 'nearest';
  }

  function scrollDeltaForPair(opts) {
    opts = opts || {};
    var pairTop = Number(opts.pairTop);
    var pairBottom = Number(opts.pairBottom);
    if (!isFinite(pairTop) || !isFinite(pairBottom)) return 0;
    var offset = Number(opts.viewportOffsetTop) || 0;
    var vh = Number(opts.viewportHeight) || 0;
    var navTop = Number(opts.navTop) || 0;
    var margin = opts.margin == null ? 8 : Number(opts.margin);
    if (!isFinite(margin)) margin = 8;
    var align = opts.align || 'nearest';
    var visualTop = offset + navTop;
    var visualBottom = offset + vh;
    var pairH = pairBottom - pairTop;
    var viewH = visualBottom - visualTop;
    if (viewH <= 0) return 0;
    if (pairH >= viewH - 2 * margin) {
      return pairTop - (visualTop + margin);
    }
    if (align === 'end') {
      return pairBottom - (visualBottom - margin);
    }
    if (align === 'start') {
      return pairTop - (visualTop + margin);
    }
    if (pairTop >= visualTop && pairBottom <= visualBottom) return 0;
    if (pairTop < visualTop) return pairTop - (visualTop + margin);
    return pairBottom - (visualBottom - margin);
  }

  function shouldScrollPairOnKeyboardClose(opts) {
    opts = opts || {};
    var pairTop = Number(opts.pairTop);
    var pairBottom = Number(opts.pairBottom);
    if (!isFinite(pairTop) || !isFinite(pairBottom)) return false;
    var offset = Number(opts.viewportOffsetTop) || 0;
    var vh = Number(opts.viewportHeight) || 0;
    var navTop = Number(opts.navTop) || 0;
    if (vh <= 0) return false;
    var visualTop = offset + navTop;
    var visualBottom = offset + vh;
    if (pairBottom < visualTop) return false;
    if (pairTop > visualBottom) return false;
    return true;
  }

  function closestFrom(el, selector) {
    if (!el) return null;
    if (el.closest) return el.closest(selector);
    return null;
  }

  function isDismissRetarget(opts) {
    opts = opts || {};
    var down = opts.pointerDownTarget;
    var input = opts.focusInput;
    if (!down || !input) return false;
    if (down === input) return false;
    if (input.contains && input.contains(down)) return false;
    var row = opts.focusRow || closestFrom(input, '.new-raddle-row');
    if (row && row.contains && row.contains(down)) return false;
    var downRow = closestFrom(down, '.new-raddle-row');
    if (downRow && downRow !== row) return false;
    var downInput = closestFrom(down, 'input.new-raddle-input');
    if (downInput && downInput !== input) return false;
    if (closestFrom(down, '.new-raddle-sticky-pin')) return false;
    return true;
  }

  global.RaddleKeyboardAnchor = {
    focusedInputIsVisible: focusedInputIsVisible,
    shouldShowStickyPin: shouldShowStickyPin,
    pairAlign: pairAlign,
    scrollDeltaForPair: scrollDeltaForPair,
    shouldScrollPairOnKeyboardClose: shouldScrollPairOnKeyboardClose,
    isDismissRetarget: isDismissRetarget,
  };
})(typeof window !== 'undefined' ? window : globalThis);
