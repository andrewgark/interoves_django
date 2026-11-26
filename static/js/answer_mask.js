/**
 * Поле ответа обычного задания по маске, как строка Лесенки.
 *
 * Сервер кладёт маску на форму ответа: data-answer-format («##### ####»,
 * «###-####»; # — клетка буквы) и data-answer-script (cyrillic, latin…),
 * см. collect_answer_masks в games/task_group/task_group_context.py. Здесь
 * поле получает серые квадратики, ширину по маске и моноширинные буквы в
 * клетках; лишнего не ввести, пробел и дефис маска ставит сама
 * (RaddleMaskedInput + IMask). Заполненная маска сразу отправляет ответ;
 * уже проверенный ответ сам не уходит снова. Сервер сравнивает ответ без
 * пробелов и знаков, так что литералы маски в значении поля не мешают.
 *
 * Карточка перерисовывается после каждого ответа, поэтому разметка
 * применяется заново при изменениях DOM.
 * Локальная проверка: node static/js/answer_mask.test.js
 */
(function (global) {
  'use strict';

  var FIELD = 'input[name="text"]:not([type="hidden"])';
  var SCRIPT_HINTS = {
    cyrillic: 'Ответ — русскими буквами',
    cyrillic_digits: 'Ответ — русскими буквами и цифрами',
    latin: 'Ответ — латинскими буквами',
    digits: 'Ответ — цифрами',
  };
  // Последний проверенный ответ каждой формы: автопроверка не шлёт его снова,
  // например когда страница возвращает в поле прошлый неверный ответ.
  var lastChecked = {};

  function M() { return global.RaddleMaskedInput; }

  function formKey(form) {
    return form.getAttribute('data-task-id') || form.id || 'answer';
  }

  function scriptOf(form) {
    return form.getAttribute('data-answer-script') || 'cyrillic';
  }

  function lettersOf(input) {
    return M().extractLetters(input.value, scriptOf(input.form));
  }

  function rememberChecked(form) {
    var input = form && form.querySelector ? form.querySelector(FIELD + '[data-answer-masked="1"]') : null;
    if (input && M()) lastChecked[formKey(form)] = lettersOf(input);
  }

  function autoCheck(input, letters) {
    var form = input.form;
    if (!form || input.readOnly || input.disabled) return false;
    if (form.dataset && form.dataset.submitting === '1') return false;
    if (lastChecked[formKey(form)] === letters) return false;
    if (typeof form.requestSubmit === 'function') form.requestSubmit();
    else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    return true;
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

  function showScriptHint(form, reason) {
    var msg = form.querySelector('.new-attempt-msg');
    var text = SCRIPT_HINTS[scriptOf(form)];
    if (!msg || reason !== 'script' || !text) return;
    msg.classList.remove('new-attempt-msg--wrong', 'new-attempt-msg--partial', 'new-attempt-msg--pending');
    msg.textContent = text;
  }

  // Smallest cell next to the buttons; below it the field takes the whole
  // row and the buttons go under it together.
  var MIN_INLINE_CELL_PX = 15;
  // The field's own padding and border (new.css, .answer-mask-input).
  var FIELD_CHROME_PX = 16;

  /** Whether the field takes a row of its own: true, false or null (unknown). */
  function stacked(rowWidth, asideWidth, cells) {
    if (!rowWidth || !cells) return null;
    return (rowWidth - asideWidth - FIELD_CHROME_PX) / cells < MIN_INLINE_CELL_PX;
  }

  // Width the answer row gives to everything but the field: «Проверить»,
  // 💡 and the gaps. The CSS sizes the cells from what is left
  // (--answer-mask-aside, .answer-mask-line in new.css).
  function measureAside(row, line) {
    var width = 0;
    var gap = parseFloat(global.getComputedStyle(row).columnGap) || 0;
    Array.prototype.forEach.call(row.children, function (el) {
      if (el === line) return;
      var rect = el.getBoundingClientRect();
      if (rect.width) width += rect.width + gap;
    });
    row.style.setProperty('--answer-mask-aside', Math.ceil(width) + 'px');
    var cells = (line.querySelector('input').getAttribute('data-raddle-format') || '').length;
    var own = stacked(row.clientWidth, width, cells);
    if (own !== null) row.classList.toggle('new-attempt-row--mask-stacked', own);
  }

  function watchAside(row, line) {
    var measure = function () { measureAside(row, line); };
    measure();
    if (global.ResizeObserver) {
      var resize = new global.ResizeObserver(measure);
      resize.observe(row);
      Array.prototype.forEach.call(row.children, function (el) { if (el !== line) resize.observe(el); });
    }
    if (typeof MutationObserver === 'function') {
      new MutationObserver(measure).observe(row, { childList: true });
    }
  }

  function applyMask(input, format, script) {
    var line = document.createElement('span');
    line.className = 'new-raddle-line answer-mask-line';
    line.style.setProperty('--raddle-format-len', String(format.length));
    // После перерисовки карточки страница сразу фокусирует новое поле, чтобы
    // не закрылась клавиатура; перенос в обёртку снимает фокус — вернём его.
    var hadFocus = document.activeElement === input;
    input.parentNode.insertBefore(line, input);
    line.appendChild(input);
    line.appendChild(maskElement(format));
    var row = line.parentNode;
    if (row.classList) row.classList.add('new-attempt-row--masked');
    if (hadFocus) {
      try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
    }

    // Класс new-raddle-input не ставим: на него завязаны сессия клавиатуры и
    // Tab-навигация Лесенки. Вид поля задают стили .answer-mask-line.
    input.setAttribute('data-raddle-format', format);
    input.setAttribute('data-raddle-script', script);
    input.placeholder = format.replace(/#/g, '◼');
    M().bindInput(input, { onComplete: autoCheck });
    watchAside(row, line);
    input.addEventListener('masked-input:reject', function (e) {
      showScriptHint(input.form, e.detail && e.detail.reason);
    });
    // The hint goes once the letters change; a dropped character still
    // fires an input event, with the letters as they were.
    var shownLetters = M().getLetters(input);
    input.addEventListener('input', function () {
      var letters = M().getLetters(input);
      if (letters === shownLetters) return;
      shownLetters = letters;
      var msg = input.form && input.form.querySelector('.new-attempt-msg');
      if (msg && msg.textContent === SCRIPT_HINTS[scriptOf(input.form)]) msg.textContent = '';
    });

    // Код страницы может вернуть в поле прошлый ответ уже после привязки
    // маски; подтягиваем маску к значению поля.
    input.addEventListener('focus', function () {
      var letters = lettersOf(input);
      if (letters !== M().getLetters(input)) M().setLetters(input, letters);
    });
  }

  function applyPlain(input, format) {
    input.placeholder = format.replace(/#/g, '◼');
    input.maxLength = format.length + 2;
  }

  function apply(root) {
    (root || document).querySelectorAll('form.new-attempt-form[data-answer-format]').forEach(function (form) {
      var input = form.querySelector(FIELD);
      if (!input || input.getAttribute('data-answer-masked') === '1') return;
      var format = form.getAttribute('data-answer-format');
      if (!format || format.indexOf('#') === -1) return;
      input.setAttribute('data-answer-masked', '1');
      input.classList.add('answer-mask-input');
      input.setAttribute('autocapitalize', 'characters');
      input.setAttribute('spellcheck', 'false');
      input.setAttribute('autocomplete', 'off');
      var count = (format.match(/#/g) || []).length;
      input.setAttribute('aria-label', 'Ответ, букв: ' + count);
      if (typeof global.IMask === 'function' && M()) applyMask(input, format, scriptOf(form));
      else applyPlain(input, format);
    });
  }

  function start() {
    apply(document);
    // Любой ответ (автопроверка, Enter, кнопка) запоминается до отправки.
    document.addEventListener('submit', function (e) { rememberChecked(e.target); }, true);
    if (typeof MutationObserver === 'function') {
      // Одна разметка на пачку изменений DOM, но до отрисовки: микрозадача.
      var queued = false;
      new MutationObserver(function () {
        if (queued) return;
        queued = true;
        Promise.resolve().then(function () { queued = false; apply(document); });
      }).observe(document.body, { childList: true, subtree: true });
    }
  }

  global.AnswerMask = {
    apply: apply,
    autoCheck: autoCheck,
    stacked: stacked,
    rememberChecked: rememberChecked,
  };

  if (typeof document === 'undefined') return;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})(typeof window !== 'undefined' ? window : globalThis);
