/*
 * Calls back once the daily result card ([data-raddle-result]) is visible.
 *
 * All four daily games render that card, but differently: ladder injects it
 * with the AJAX task HTML, salad swaps HTML, alphabet and censorly unhide it.
 * Watching the DOM covers all of them. Censorly does not forward the
 * game_complete goal to the browser, so the analytics event is not enough.
 *
 * Usage: window.interovesOnDailyResult(function () { ... });
 */
(function (global) {
  'use strict';
  if (global.interovesOnDailyResult) return;
  var SELECTOR = '[data-raddle-result]:not([hidden])';
  var callbacks = [];
  var fired = false;
  var observer = null;

  function check() {
    if (fired || !document.querySelector(SELECTOR)) return;
    fired = true;
    if (observer) observer.disconnect();
    callbacks.splice(0).forEach(function (cb) {
      try { cb(); } catch (e) {}
    });
  }

  function start() {
    check();
    if (fired || !global.MutationObserver || !document.body) return;
    observer = new MutationObserver(check);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['hidden'],
    });
  }

  global.interovesOnDailyResult = function (cb) {
    if (fired) {
      try { cb(); } catch (e) {}
      return;
    }
    callbacks.push(cb);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})(window);
