/**
 * Голосовалка ▲▼ (.new-vote): после смены чисел/«моего голоса» пересчитывает
 * data-lead (кто ведёт), data-zero у нулей и aria-pressed по .is-active.
 */
(function () {
  function count(el) {
    var n = parseInt(el ? el.textContent : '0', 10);
    return isNaN(n) ? 0 : n;
  }
  function lead(up, down) {
    if (up > down) return 'up';
    if (down > up) return 'down';
    return up ? 'tie' : 'none';
  }
  function refresh(scope) {
    (scope || document).querySelectorAll('[data-vote]').forEach(function (vote) {
      var counts = vote.querySelectorAll('.new-vote__count');
      counts.forEach(function (el) {
        if (count(el)) el.removeAttribute('data-zero');
        else el.setAttribute('data-zero', '');
      });
      vote.setAttribute('data-lead', lead(count(counts[0]), count(counts[1])));
      vote.querySelectorAll('button.new-vote__side').forEach(function (btn) {
        btn.setAttribute('aria-pressed', btn.classList.contains('is-active') ? 'true' : 'false');
      });
    });
  }
  window.InterovesVoteStack = { refresh: refresh };
})();
