/**
 * Daily game header: on narrow screens the title is single-line with an
 * ellipsis. While it is still truncated, step the header down to smaller
 * controls and title (CSS levels .is-fit-tight / .is-fit-tighter).
 *
 * Loaded (without defer) from base.html before the page content. While the
 * page is parsed, a MutationObserver fits the header as soon as its markup
 * arrives, before the browser paints it: the full-size header never flashes
 * on load. A deferred or late script would run after that first paint.
 */
(function () {
  'use strict';

  var HEADER = '[data-daily-game-header]';
  var LEVELS = ['is-fit-tight', 'is-fit-tighter'];

  function isTruncated(title) {
    return title.scrollWidth > title.clientWidth + 1;
  }

  function fit(header) {
    var title = header.querySelector('.new-heading');
    if (!title || !header.getClientRects().length) return;
    LEVELS.forEach(function (cls) { header.classList.remove(cls); });
    for (var i = 0; i < LEVELS.length && isTruncated(title); i++) {
      if (i > 0) header.classList.remove(LEVELS[i - 1]);
      header.classList.add(LEVELS[i]);
    }
  }

  var frame = 0;
  function fitAll() {
    frame = 0;
    document.querySelectorAll(HEADER).forEach(fit);
  }
  function schedule() {
    if (!frame) frame = window.requestAnimationFrame(fitAll);
  }

  // Timer/replay controls change after load and take width from the title.
  var resize = window.ResizeObserver ? new ResizeObserver(schedule) : null;
  var watched = typeof WeakSet === 'function' ? new WeakSet() : null;
  function watch(header) {
    var controls = header.querySelector('.new-daily-game-header__controls');
    if (!resize || !watched || !controls || watched.has(controls)) return;
    watched.add(controls);
    resize.observe(controls);
  }

  var parsing = null;
  if (document.readyState === 'loading' && window.MutationObserver && document.body) {
    parsing = new MutationObserver(function (records) {
      var touched = [];
      records.forEach(function (rec) {
        var host = rec.target.nodeType === 1 ? rec.target : rec.target.parentElement;
        var header = host && host.closest(HEADER);
        if (header) {
          if (touched.indexOf(header) < 0) touched.push(header);
          return;
        }
        Array.prototype.forEach.call(rec.addedNodes, function (node) {
          if (node.nodeType !== 1) return;
          var found = node.matches(HEADER) ? [node] : node.querySelectorAll(HEADER);
          Array.prototype.forEach.call(found, function (h) {
            if (touched.indexOf(h) < 0) touched.push(h);
          });
        });
      });
      touched.forEach(fit);
    });
    parsing.observe(document.body, { childList: true, subtree: true });
  }

  function ready() {
    if (parsing) { parsing.disconnect(); parsing = null; }
    document.querySelectorAll(HEADER).forEach(function (header) { watch(header); fit(header); });
  }

  window.InterovesDailyHeaderFit = { fit: fitAll };
  window.addEventListener('resize', schedule);
  // Fit in the same task the fonts arrive in, not a frame later.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitAll);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }
})();
