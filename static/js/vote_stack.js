/**
 * Голосовалка ▲▼ (.new-vote): после смены чисел/«моего голоса» пересчитывает
 * data-lead (кто ведёт), data-zero у нулей и aria-pressed по .is-active.
 *
 * Счётчики показываются коротко (1,2K): точное число лежит в data-count.
 * Страницы после голоса пишут в счётчик обычное число через textContent —
 * refresh() запоминает его в data-count и сокращает.
 */
(function () {
  /* Совпадает с фильтром vote_count (games/site/http/tags/filters.py). */
  function shortCount(n) {
    var steps = [[1000000, 'M'], [1000, 'K']];
    for (var i = 0; i < steps.length; i++) {
      var base = steps[i][0];
      var suffix = steps[i][1];
      if (n < base) continue;
      if (n < base * 10) {
        var tenths = Math.floor(n * 10 / base);
        var whole = Math.floor(tenths / 10);
        var frac = tenths % 10;
        return frac ? whole + ',' + frac + suffix : whole + suffix;
      }
      if (n < base * 1000 || suffix === 'M') return Math.floor(n / base) + suffix;
    }
    return String(n);
  }
  function count(el) {
    if (!el) return 0;
    var text = String(el.textContent || '').trim();
    var n;
    if (/^\d+$/.test(text)) {
      n = parseInt(text, 10);
      el.setAttribute('data-count', String(n));
    } else {
      n = parseInt(el.getAttribute('data-count') || '0', 10);
    }
    if (isNaN(n)) n = 0;
    var label = shortCount(n);
    if (text !== label) el.textContent = label;
    return n;
  }
  function lead(up, down) {
    if (up > down) return 'up';
    if (down > up) return 'down';
    return up ? 'tie' : 'none';
  }
  function refresh(scope) {
    (scope || document).querySelectorAll('[data-vote]').forEach(function (vote) {
      var counts = vote.querySelectorAll('.new-vote__count');
      var values = [];
      counts.forEach(function (el) {
        var n = count(el);
        values.push(n);
        if (n) el.removeAttribute('data-zero');
        else el.setAttribute('data-zero', '');
      });
      vote.setAttribute('data-lead', lead(values[0] || 0, values[1] || 0));
      vote.querySelectorAll('button.new-vote__side').forEach(function (btn) {
        btn.setAttribute('aria-pressed', btn.classList.contains('is-active') ? 'true' : 'false');
      });
    });
  }
  window.InterovesVoteStack = { refresh: refresh, shortCount: shortCount };
})();
