(function () {
  'use strict';

  var root = document.querySelector('[data-vote-page]');
  if (!root) return;

  var phase = root.getAttribute('data-vote-phase') || '';
  var deadlineRaw = root.getAttribute('data-vote-deadline') || '';

  function pad(value) {
    return value < 10 ? '0' + value : String(value);
  }

  function formatRemaining(ms) {
    if (ms <= 0) return '0:00:00';
    var totalSec = Math.floor(ms / 1000);
    var days = Math.floor(totalSec / 86400);
    var hours = Math.floor((totalSec % 86400) / 3600);
    var minutes = Math.floor((totalSec % 3600) / 60);
    var seconds = totalSec % 60;
    var clock = pad(hours) + ':' + pad(minutes) + ':' + pad(seconds);
    if (days > 0) return days + ' д. ' + clock;
    return clock;
  }

  function tickCountdown() {
    var el = root.querySelector('[data-vote-countdown-value]');
    if (!el || !deadlineRaw) return;
    var end = Date.parse(deadlineRaw);
    if (!isFinite(end)) return;
    var remaining = end - Date.now();
    if (remaining <= 0) {
      el.textContent = '0:00:00';
      return;
    }
    el.textContent = formatRemaining(remaining);
  }

  if (phase === 'live') {
    tickCountdown();
    window.setInterval(tickCountdown, 1000);
  }

  function trackClick(candidate) {
    if (!candidate) return;
    if (!window.interovesAnalytics || typeof window.interovesAnalytics.trackYandexGoalOnce !== 'function') {
      return;
    }
    window.interovesAnalytics.trackYandexGoalOnce(
      'next_game_vote_click:' + candidate,
      'next_game_vote_click',
      { candidate: candidate }
    );
  }

  root.addEventListener('click', function (event) {
    var link = event.target && event.target.closest ? event.target.closest('[data-vote-cta]') : null;
    if (!link) return;
    trackClick(link.getAttribute('data-vote-cta') || '');
  });
})();
