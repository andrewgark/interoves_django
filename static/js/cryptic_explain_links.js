/* Разбор криптика: наведение на шаг подсвечивает его индикатор и буквы в
   подсказке, и наоборот. Связи — общие номера в data-cx-ids внутри .cx-explain. */
(function () {
  var pending = null;

  function ids(el) {
    return (el.getAttribute('data-cx-ids') || '').split(' ').filter(Boolean);
  }

  function clear(root) {
    root.classList.remove('cx-linking');
    var lit = root.querySelectorAll('.cx-on');
    for (var i = 0; i < lit.length; i++) lit[i].classList.remove('cx-on');
  }

  function clearAll(except) {
    var roots = document.querySelectorAll('.cx-explain.cx-linking');
    for (var i = 0; i < roots.length; i++) if (roots[i] !== except) clear(roots[i]);
  }

  function light(el) {
    var root = el.closest('.cx-explain');
    if (!root) return;
    clearAll(root);
    clear(root);
    var want = ids(el);
    var all = root.querySelectorAll('[data-cx-ids]');
    for (var i = 0; i < all.length; i++) {
      var mine = ids(all[i]);
      for (var j = 0; j < mine.length; j++) {
        if (want.indexOf(mine[j]) >= 0) { all[i].classList.add('cx-on'); break; }
      }
    }
    root.classList.add('cx-linking');
  }

  document.addEventListener('pointerover', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('[data-cx-ids]') : null;
    if (pending) { clearTimeout(pending); pending = null; }
    if (el) { light(el); return; }
    // Между колонками шага есть зазор: гасим с задержкой, чтобы не мигало.
    pending = setTimeout(function () { clearAll(null); }, 120);
  });
  document.addEventListener('pointerout', function (e) {
    if (!e.relatedTarget) clearAll(null);
  });
})();
