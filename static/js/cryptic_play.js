// Криптики: поле ответа повторяет маску из подсказки — «(6)» → ◼◼◼◼◼◼,
// «(3-4)» → ◼◼◼-◼◼◼◼, «(5, 4)» → ◼◼◼◼◼ ◼◼◼◼. Карточка перерисовывается после
// каждого ответа, поэтому разметка применяется заново при изменениях DOM.
(function () {
  function maskFor(enumeration) {
    return String(enumeration || '')
      .replace(/\s+/g, '')
      .split(/([,-])/)
      .map(function (part) {
        if (part === ',') return ' ';
        if (part === '-') return '-';
        var n = parseInt(part, 10);
        return n > 0 && n < 100 ? new Array(n + 1).join('◼') : '';
      })
      .join('');
  }

  function letters(enumeration) {
    return String(enumeration || '').split(/[,\s-]+/).reduce(function (sum, part) {
      var n = parseInt(part, 10);
      return sum + (n > 0 ? n : 0);
    }, 0);
  }

  function apply(root) {
    (root || document).querySelectorAll('[data-cryptic-enum]').forEach(function (badge) {
      var card = badge.closest('.new-taskcard, .new-task, [id^="new-task-"]') || document;
      var input = card.querySelector('form.new-attempt-form input[name="text"]:not([type="hidden"])');
      if (!input || input.getAttribute('data-cryptic-masked') === '1') return;
      var enumeration = badge.getAttribute('data-cryptic-enum');
      var mask = maskFor(enumeration);
      if (!mask) return;
      input.setAttribute('data-cryptic-masked', '1');
      input.classList.add('cryptic-answer-input');
      input.placeholder = mask;
      input.maxLength = mask.length + 2;
      input.setAttribute('autocapitalize', 'characters');
      input.setAttribute('spellcheck', 'false');
      input.setAttribute('autocomplete', 'off');
      var count = letters(enumeration);
      input.setAttribute('aria-label', 'Ответ, ' + enumeration + (count ? ' (букв: ' + count + ')' : ''));
    });
  }

  function start() {
    apply(document);
    if (typeof MutationObserver === 'function') {
      new MutationObserver(function () { apply(document); })
        .observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
