/**
 * Daily game header: on narrow screens the title is single-line with an
 * ellipsis. While it is still truncated, step the header down to smaller
 * controls and title (CSS levels .is-fit-tight / .is-fit-tighter).
 */
(function () {
  'use strict';

  var LEVELS = ['is-fit-tight', 'is-fit-tighter'];

  function isTruncated(title) {
    return title.scrollWidth > title.clientWidth + 1;
  }

  function fit(header) {
    var title = header.querySelector('.new-heading');
    if (!title) return;
    LEVELS.forEach(function (cls) { header.classList.remove(cls); });
    for (var i = 0; i < LEVELS.length && isTruncated(title); i++) {
      if (i > 0) header.classList.remove(LEVELS[i - 1]);
      header.classList.add(LEVELS[i]);
    }
  }

  function init() {
    var headers = document.querySelectorAll('[data-daily-game-header]');
    if (!headers.length) return;
    var frame = 0;
    function fitAll() {
      frame = 0;
      headers.forEach(fit);
    }
    function schedule() {
      if (!frame) frame = window.requestAnimationFrame(fitAll);
    }
    fitAll();
    window.addEventListener('resize', schedule);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
    if (window.ResizeObserver) {
      // Timer/replay controls appear after load and take width from the title.
      var observer = new ResizeObserver(schedule);
      headers.forEach(function (header) {
        var controls = header.querySelector('.new-daily-game-header__controls');
        if (controls) observer.observe(controls);
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
