// Криптики: поле ответа устроено как строка Лесенки — серые квадратики маски
// из подсказки («(6)» → ◼◼◼◼◼◼, «(3-4)» → ◼◼◼-◼◼◼◼, «(5, 4)» → ◼◼◼◼◼ ◼◼◼◼),
// ширина по длине ответа, моноширинные буквы ложатся в слоты, лишние не
// вводятся, дефис и пробел маска ставит сама (RaddleMaskedInput + IMask).
// Сервер сравнивает ответ без пробелов и знаков, так что литералы маски в
// значении поля не мешают. Карточка перерисовывается после каждого ответа,
// поэтому разметка применяется заново при изменениях DOM.
(function () {
  // «5, 4» → «##### ####», «3-4» → «###-####». Пустая строка — маска непонятна.
  function formatFor(enumeration) {
    var parts = String(enumeration || '').replace(/\s+/g, '').split(/([,-])/);
    var out = '';
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i];
      if (part === ',') out += ' ';
      else if (part === '-') out += '-';
      else {
        var n = parseInt(part, 10);
        if (!(n > 0 && n < 100)) return '';
        out += new Array(n + 1).join('#');
      }
    }
    return out;
  }

  function slotCount(format) {
    return (format.match(/#/g) || []).length;
  }

  function maskElement(format) {
    var mask = document.createElement('span');
    mask.className = 'new-raddle-mask';
    mask.setAttribute('aria-hidden', 'true');
    for (var i = 0; i < format.length; i++) {
      var ch = format.charAt(i);
      var cell = document.createElement('span');
      cell.className = ch === '#' ? 'new-raddle-mask__slot' : 'new-raddle-mask__literal';
      cell.textContent = ch === '#' ? '◼' : ch;
      mask.appendChild(cell);
    }
    return mask;
  }

  function applyMask(input, format) {
    var line = document.createElement('span');
    line.className = 'new-raddle-line cryptic-answer-line';
    line.style.setProperty('--raddle-format-len', String(format.length));
    // После перерисовки карточки страница сразу фокусирует новое поле, чтобы
    // не закрылась клавиатура; перенос в обёртку снимает фокус — вернём его.
    var hadFocus = document.activeElement === input;
    input.parentNode.insertBefore(line, input);
    line.appendChild(input);
    line.appendChild(maskElement(format));
    if (hadFocus) {
      try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
    }

    // Класс new-raddle-input не ставим: на него завязаны сессия клавиатуры и
    // Tab-навигация Лесенки. Вид поля задают стили .cryptic-answer-line.
    input.setAttribute('data-raddle-format', format);
    // Ответ может оказаться и латиницей, и с цифрами: маска пускает любые буквы.
    input.setAttribute('data-raddle-script', 'mixed_digits');
    input.placeholder = format.replace(/#/g, '◼');
    window.RaddleMaskedInput.bindInput(input, {});

    // Код страницы может вернуть в поле прошлый неверный ответ уже после
    // привязки маски; подтягиваем маску к значению поля.
    input.addEventListener('focus', function () {
      var M = window.RaddleMaskedInput;
      var letters = M.extractLetters(input.value, 'mixed_digits');
      if (letters !== M.getLetters(input)) M.setLetters(input, letters);
    });
  }

  function applyPlain(input, format) {
    input.placeholder = format.replace(/#/g, '◼');
    input.maxLength = format.length + 2;
  }

  function apply(root) {
    (root || document).querySelectorAll('[data-cryptic-enum]').forEach(function (badge) {
      var card = badge.closest('.new-taskcard, .new-task, [id^="new-task-"]') || document;
      var input = card.querySelector('form.new-attempt-form input[name="text"]:not([type="hidden"])');
      if (!input || input.getAttribute('data-cryptic-masked') === '1') return;
      var enumeration = badge.getAttribute('data-cryptic-enum');
      var format = formatFor(enumeration);
      if (!format) return;
      input.setAttribute('data-cryptic-masked', '1');
      input.classList.add('cryptic-answer-input');
      input.setAttribute('autocapitalize', 'characters');
      input.setAttribute('spellcheck', 'false');
      input.setAttribute('autocomplete', 'off');
      var count = slotCount(format);
      input.setAttribute('aria-label', 'Ответ, ' + enumeration + ' (букв: ' + count + ')');
      if (typeof window.IMask === 'function' && window.RaddleMaskedInput) applyMask(input, format);
      else applyPlain(input, format);
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
