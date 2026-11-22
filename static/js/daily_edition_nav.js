/**
 * The edition arrows of a daily game header (site/partials/daily_game_header.html).
 *
 * Finishing a game nudges the open arrows once: there is more to play. Not on
 * a return visit to a finished edition, whose result card is already shown by
 * DOMContentLoaded. Finishing the newest edition also clears this game's
 * «not finished» dot in the site nav.
 *
 * Loaded as a plain (not deferred) script right after the arrows, so it runs
 * while the page is still parsing and waits for DOMContentLoaded, after the
 * games' own deferred scripts have drawn the result card.
 */
(function () {
  'use strict';
  var header = document.currentScript && document.currentScript.closest('[data-daily-game-header]');
  var nav = header && header.querySelector('.new-daily-game-header__nav');
  if (!nav) return;
  var gameId = header.getAttribute('data-game-id') || '';
  var newest = header.hasAttribute('data-newest-edition');

  function clearNavDot() {
    document.querySelectorAll('.new-nav__section[data-nav-game="' + gameId + '"]').forEach(function (link) {
      var dot = link.querySelector('.new-fresh-dot');
      if (!dot) return;
      dot.remove();
      link.setAttribute('aria-label', (link.getAttribute('aria-label') || '').replace(', новый выпуск ещё не решён', ''));
    });
  }

  function nudge() {
    window.setTimeout(function () {
      nav.classList.add('is-calling');
      window.setTimeout(function () { nav.classList.remove('is-calling'); }, 3000);
    }, 900);
  }

  function start() {
    if (!window.interovesOnDailyResult) return;
    var finishedBefore = !!document.querySelector('[data-raddle-result]:not([hidden])');
    window.interovesOnDailyResult(function () {
      if (newest && gameId) clearNavDot();
      if (finishedBefore) return;
      if (nav.querySelector('a.new-daily-game-header__nav-button:not(.is-archive-locked)')) nudge();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
