/**
 * Маскированный ввод для лесенки (raddle): только русские буквы,
 * разделители (дефис, пробел, запятая…) подставляются по шаблону с сервера.
 * Шаблон: # — слот буквы, остальные символы — фиксированные литералы.
 * Локальная проверка: node static/js/raddle_masked_input.test.js
 */
(function (global) {
  'use strict';

  var SLOT = '#';
  var CYRILLIC_LETTER_RE = /[а-яёА-ЯЁ]/;
  var CYRILLIC_EXTRACT_RE = /[а-яёА-ЯЁ]/g;
  var BOUND = 'data-raddle-mask-bound';

  function slotCount(fmt) {
    var n = 0;
    for (var i = 0; i < fmt.length; i++) {
      if (fmt.charAt(i) === SLOT) n++;
    }
    return n;
  }

  function normalizeLetter(ch) {
    return String(ch || '').replace(/ё/gi, function (m) {
      return m === 'ё' ? 'е' : 'Е';
    }).toUpperCase();
  }

  function extractRussianLetters(text) {
    var m = String(text || '').match(CYRILLIC_EXTRACT_RE);
    if (!m) return '';
    return m.map(normalizeLetter).join('');
  }

  function lettersToDisplay(fmt, letters) {
    var li = 0;
    var out = '';
    for (var i = 0; i < fmt.length; i++) {
      var ch = fmt.charAt(i);
      if (ch === SLOT) {
        if (li < letters.length) out += letters.charAt(li++);
      } else {
        var need = 0;
        for (var j = 0; j < i; j++) {
          if (fmt.charAt(j) === SLOT) need++;
        }
        if (letters.length >= need) out += ch;
      }
    }
    return out;
  }

  function getLetters(input) {
    return input.dataset.raddleLetters || '';
  }

  function setLetters(input, letters, opts) {
    opts = opts || {};
    var fmt = input.getAttribute('data-raddle-format') || '';
    var max = slotCount(fmt);
    var clean = extractRussianLetters(letters).slice(0, max);
    input.dataset.raddleLetters = clean;
    var display = lettersToDisplay(fmt, clean);
    input.value = display;
    try {
      input.setSelectionRange(display.length, display.length);
    } catch (e) {}
    if (typeof opts.onChange === 'function') opts.onChange(input, clean);
    if (clean.length === max && typeof opts.onComplete === 'function') {
      opts.onComplete(input, clean);
    }
    return clean;
  }

  function getSubmitValue(input) {
    if (!input) return '';
    if (input.getAttribute('data-raddle-format')) return getLetters(input);
    return String(input.value || '').trim();
  }

  function bindInput(input, hooks) {
    if (!input || input.getAttribute(BOUND) === '1') return;
    var fmt = input.getAttribute('data-raddle-format') || '';
    if (!fmt) return;
    input.setAttribute(BOUND, '1');
    input.setAttribute('inputmode', 'text');
    input.setAttribute('autocapitalize', 'characters');
    input.removeAttribute('maxlength');

    var initial = input.getAttribute('value') || input.value || '';
    if (initial) {
      setLetters(input, extractRussianLetters(initial), {});
    } else {
      setLetters(input, '', {});
    }
    input.removeAttribute('value');

    var composing = false;

    function reconcileFromValue() {
      var letters = getLetters(input);
      var max = slotCount(fmt);
      var reconciled = extractRussianLetters(input.value).slice(0, max);
      if (reconciled !== letters) {
        setLetters(input, reconciled, hooks);
      }
    }

    input.addEventListener('compositionstart', function () {
      composing = true;
    });
    input.addEventListener('compositionend', function () {
      composing = false;
      reconcileFromValue();
    });

    input.addEventListener('keydown', function (e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      var allowed = {
        Tab: 1, Enter: 1, Escape: 1,
        ArrowLeft: 1, ArrowRight: 1, ArrowUp: 1, ArrowDown: 1,
        Home: 1, End: 1,
      };
      if (allowed[e.key]) return;

      var letters = getLetters(input);
      var max = slotCount(fmt);

      if (e.key === 'Backspace') {
        e.preventDefault();
        if (letters.length) setLetters(input, letters.slice(0, -1), hooks);
        return;
      }
      if (e.key === 'Delete') {
        e.preventDefault();
        return;
      }
      if (e.key.length === 1) {
        e.preventDefault();
        if (!CYRILLIC_LETTER_RE.test(e.key)) return;
        if (letters.length >= max) return;
        setLetters(input, letters + normalizeLetter(e.key), hooks);
      }
    });

    input.addEventListener('paste', function (e) {
      e.preventDefault();
      var pasted = '';
      try {
        pasted = e.clipboardData && e.clipboardData.getData('text');
      } catch (err) {}
      var letters = getLetters(input);
      setLetters(input, letters + extractRussianLetters(pasted), hooks);
    });

    input.addEventListener('drop', function (e) {
      e.preventDefault();
    });

    // Мобильная клавиатура часто шлёт input/composition без keydown.
    input.addEventListener('input', function (e) {
      if (e.isComposing || composing) return;
      reconcileFromValue();
    });
  }

  function bindAll(root, hooks) {
    var scope = root || document;
    scope.querySelectorAll('input[name="word"][data-raddle-format]').forEach(function (input) {
      bindInput(input, hooks);
    });
  }

  global.RaddleMaskedInput = {
    SLOT: SLOT,
    slotCount: slotCount,
    extractRussianLetters: extractRussianLetters,
    lettersToDisplay: lettersToDisplay,
    getLetters: getLetters,
    setLetters: setLetters,
    getSubmitValue: getSubmitValue,
    bindInput: bindInput,
    bindAll: bindAll,
  };
})(typeof window !== 'undefined' ? window : global);
