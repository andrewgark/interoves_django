/**
 * Task meta bar (.new-taskcard__meta-bar) must stay one line. Pick the least
 * compact level that still fits and store it in data-fit (CSS in new.css):
 *   full    — «(3 попытки)», five brains
 *   compact — units and brackets go to tooltips: «↻3», «подсказки» / «ответ»
 *             become icons (as in Лесенка), five brains
 *   tight   — difficulty collapses to one brain and a digit
 *   tiny    — tighter gaps, points without the pill frame
 * The bar is re-rendered by Alphabetty/Censorly after a guess, so new bars are
 * fitted from a MutationObserver (runs before paint, no flash of «full»).
 * A ResizeObserver refits a bar whose width changed — including a card that
 * was hidden (hide solved, tabs) and is shown again.
 */
(function () {
  'use strict';

  var LEVELS = ['full', 'compact', 'tight', 'tiny'];
  var BAR = '.new-taskcard__meta-bar';

  function items(bar) {
    var out = [];
    Array.prototype.forEach.call(bar.children, function (el) {
      var group = el.classList.contains('new-proportions-compact-stats') ||
        el.classList.contains('new-proportions-compact-bar__aside');
      Array.prototype.push.apply(out, group ? el.children : [el]);
    });
    // An open attempts popover lives in the top layer, not in the line.
    return out.filter(function (el) { return !el.hasAttribute('popover'); });
  }

  /* Wrapped = some box starts below the bottom of another one. Text line
     boxes count too: a long item (raddle/wall stats) can wrap inside itself
     while its own box still sits in the first line. */
  function isWrapped(bar) {
    var minBottom = Infinity;
    var maxTop = -Infinity;
    var range = document.createRange ? document.createRange() : null;
    function add(r) {
      if (!r.width || !r.height) return;
      if (r.bottom < minBottom) minBottom = r.bottom;
      if (r.top > maxTop) maxTop = r.top;
    }
    items(bar).forEach(function (el) {
      add(el.getBoundingClientRect());
      if (!range) return;
      range.selectNodeContents(el);
      Array.prototype.forEach.call(range.getClientRects(), add);
    });
    return maxTop >= minBottom - 1;
  }

  var observer = null;
  var widths = typeof WeakMap === 'function' ? new WeakMap() : null;

  function fit(bar) {
    if (observer && widths && !widths.has(bar)) {
      widths.set(bar, -1);
      observer.observe(bar);
    }
    if (!bar.isConnected || !bar.getClientRects().length) return;
    if (widths) widths.set(bar, bar.clientWidth);
    for (var i = 0; i < LEVELS.length; i++) {
      bar.setAttribute('data-fit', LEVELS[i]);
      if (!isWrapped(bar)) return;
    }
  }

  function fitAll(scope) {
    var root = scope || document;
    if (root.matches && root.matches(BAR)) fit(root);
    if (root.querySelectorAll) Array.prototype.forEach.call(root.querySelectorAll(BAR), fit);
  }

  function init() {
    if (window.ResizeObserver && widths) {
      // Only width matters: a level change alters the height and must not
      // refit again (no observer loop).
      observer = new ResizeObserver(function (entries) {
        entries.forEach(function (entry) {
          var bar = entry.target;
          if (bar.clientWidth !== widths.get(bar)) fit(bar);
        });
      });
    }
    var frame = 0;
    var width = window.innerWidth;
    function schedule() {
      if (!frame) frame = window.requestAnimationFrame(function () { frame = 0; fitAll(); });
    }
    fitAll();
    window.addEventListener('resize', function () {
      if (window.innerWidth === width) return; // mobile URL bar show/hide
      width = window.innerWidth;
      schedule();
    });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
    if (window.MutationObserver) {
      new MutationObserver(function (records) {
        records.forEach(function (rec) {
          Array.prototype.forEach.call(rec.addedNodes, function (node) {
            if (node.nodeType === 1) fitAll(node);
          });
          // A replaced bar must not stay pinned by the ResizeObserver.
          if (observer) Array.prototype.forEach.call(rec.removedNodes, function (node) {
            if (node.nodeType === 1 && node.matches(BAR)) observer.unobserve(node);
          });
          // Counts/labels inside an existing bar changed (votes, attempts).
          var host = rec.target.nodeType === 1 ? rec.target : rec.target.parentElement;
          var bar = host && host.closest && host.closest(BAR);
          if (bar) fit(bar);
        });
      }).observe(document.body, { childList: true, subtree: true, characterData: true });
    }
  }

  window.InterovesTaskMetaFit = { fit: fitAll };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
