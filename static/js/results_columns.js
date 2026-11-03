/*
 * Fit the frozen columns of results tables to their content.
 *
 * The frozen (position: sticky) columns are sized only through the
 * --results-col-* custom properties, and their `left` offsets are calc()s of
 * the same properties (static/css/new.css). Setting the properties on the
 * table therefore resizes a column and moves every column after it together:
 * the frozen block cannot overlap or leave gaps.
 *
 * Widths come from the laid-out text (Range rects), which works although the
 * cells clip their content. Only the name column is capped; the long tail of
 * names keeps its ellipsis and tooltip.
 */
(function () {
  'use strict';

  var MIN = { rank: 22, team: 84, score: 26, time: 30, attempts: 26 };
  var TEAM_CAP_DESKTOP = 288;  // 18rem at the base font size
  var TEAM_CAP_SHARE = 0.45;   // of the scroll area on narrow screens
  var NARROW = 640;            // must match the results table media query

  function cellNeed(cell) {
    var range = document.createRange();
    range.selectNodeContents(cell);
    var text = range.getBoundingClientRect().width;
    var style = window.getComputedStyle(cell);
    return Math.ceil(
      text
      + parseFloat(style.paddingLeft) + parseFloat(style.paddingRight)
      + parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth)
      + 1
    );
  }

  function maxNeed(cells) {
    var best = 0;
    for (var i = 0; i < cells.length; i += 1) {
      var need = cellNeed(cells[i]);
      if (need > best) best = need;
    }
    return best;
  }

  function teamCap(table) {
    // The scroll wrapper is inline-block and shrinks to the table, so the
    // room on screen is measured on the full-bleed container around it.
    var outer = table.closest('.new-results-fullbleed');
    var available = window.innerWidth;
    if (outer) {
      var style = window.getComputedStyle(outer);
      available = outer.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    }
    if (window.innerWidth <= NARROW) {
      return Math.max(MIN.team, Math.floor(available * TEAM_CAP_SHARE));
    }
    return TEAM_CAP_DESKTOP;
  }

  function fit(table) {
    // Read everything first, then write once: no layout thrashing.
    var rank = table.querySelectorAll('.is-sticky-left');
    var team = table.querySelectorAll('.is-sticky-left-2');
    var score = table.querySelectorAll('.is-sticky-left-3');
    var time = table.querySelectorAll('.is-sticky-left-4');
    var attempts = table.querySelectorAll('.results-attempts');
    if (!rank.length || !team.length) return;

    var sizes = {
      '--results-col-rank': Math.max(MIN.rank, maxNeed(rank)),
      '--results-col-team': Math.min(teamCap(table), Math.max(MIN.team, maxNeed(team))),
    };
    if (score.length) sizes['--results-col-score'] = Math.max(MIN.score, maxNeed(score));
    if (time.length) sizes['--results-col-time'] = Math.max(MIN.time, maxNeed(time));
    if (attempts.length) {
      sizes['--results-col-attempts'] = Math.max(MIN.attempts, maxNeed(attempts));
      // Alphabetty: attempts sit in the score slot, so both widths must agree
      // or the next frozen column's offset would not match.
      if (score.length && score[0].classList.contains('results-attempts')) {
        var shared = Math.max(sizes['--results-col-score'], sizes['--results-col-attempts']);
        sizes['--results-col-score'] = shared;
        sizes['--results-col-attempts'] = shared;
      }
    }

    Object.keys(sizes).forEach(function (name) {
      var value = sizes[name] + 'px';
      if (table.style.getPropertyValue(name) !== value) {
        table.style.setProperty(name, value);
      }
    });
    table.classList.add('is-columns-fitted');
  }

  function setup(table) {
    var scheduled = false;
    function schedule() {
      if (scheduled) return;
      scheduled = true;
      window.requestAnimationFrame(function () {
        scheduled = false;
        fit(table);
      });
    }
    fit(table);
    window.addEventListener('resize', schedule);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
    // «Показать ещё» appends rows: longer names may arrive with them.
    var tbody = table.querySelector('tbody');
    if (tbody && typeof MutationObserver !== 'undefined') {
      new MutationObserver(schedule).observe(tbody, { childList: true });
    }
  }

  function init() {
    document.querySelectorAll('.new-results-table').forEach(setup);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
