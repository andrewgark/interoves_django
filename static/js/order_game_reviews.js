(function () {
  var AUTO_MIN_MS = 5000;
  var AUTO_MAX_MS = 10000;
  var PAUSE_AFTER_INTERACTION_MS = 15000;

  function initCarousel(root) {
    var viewport = root.querySelector('[data-corp-reviews-viewport]');
    var track = root.querySelector('[data-corp-reviews-track]');
    var prevBtn = root.querySelector('[data-corp-reviews-prev]');
    var nextBtn = root.querySelector('[data-corp-reviews-next]');
    if (!viewport || !track) {
      return;
    }

    var cards = Array.prototype.slice.call(track.children);
    if (!cards.length) {
      return;
    }

    var autoTimer = null;
    var pauseUntil = 0;

    function getStep() {
      var card = cards[0];
      var gap = parseFloat(window.getComputedStyle(track).columnGap || window.getComputedStyle(track).gap) || 0;
      return card.offsetWidth + gap;
    }

    function maxScrollLeft() {
      return Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    }

    function visibleCount() {
      var step = getStep();
      if (!step) {
        return 1;
      }
      return Math.max(1, Math.round(viewport.clientWidth / step));
    }

    function updateButtons() {
      var canScroll = cards.length > visibleCount();
      if (prevBtn) {
        prevBtn.disabled = !canScroll;
      }
      if (nextBtn) {
        nextBtn.disabled = !canScroll;
      }
    }

    function pauseAuto() {
      pauseUntil = Date.now() + PAUSE_AFTER_INTERACTION_MS;
      scheduleAuto();
    }

    function scheduleAuto() {
      if (autoTimer) {
        window.clearTimeout(autoTimer);
      }
      if (cards.length <= visibleCount()) {
        return;
      }
      var delay = AUTO_MIN_MS + Math.random() * (AUTO_MAX_MS - AUTO_MIN_MS);
      autoTimer = window.setTimeout(function () {
        if (Date.now() < pauseUntil) {
          scheduleAuto();
          return;
        }
        scrollBy(1);
        scheduleAuto();
      }, delay);
    }

    function scrollBy(direction) {
      var step = getStep();
      var max = maxScrollLeft();
      var target = viewport.scrollLeft + direction * step;
      if (target > max + 2) {
        target = 0;
      } else if (target < -2) {
        target = max;
      }
      viewport.scrollTo({ left: target, behavior: 'smooth' });
    }

    if (prevBtn) {
      prevBtn.addEventListener('click', function () {
        scrollBy(-1);
        pauseAuto();
      });
    }
    if (nextBtn) {
      nextBtn.addEventListener('click', function () {
        scrollBy(1);
        pauseAuto();
      });
    }

    viewport.addEventListener('pointerdown', pauseAuto);
    viewport.addEventListener('wheel', pauseAuto, { passive: true });
    viewport.addEventListener('touchstart', pauseAuto, { passive: true });

    window.addEventListener('resize', updateButtons);
    updateButtons();
    scheduleAuto();
  }

  document.addEventListener('DOMContentLoaded', function () {
    Array.prototype.forEach.call(
      document.querySelectorAll('[data-corp-reviews-carousel]'),
      initCarousel
    );
  });
})();
