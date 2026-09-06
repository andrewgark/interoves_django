/**
 * Маскированный ввод для лесенки (raddle): IMask.js + шаблон с сервера.
 * Шаблон: # — слот буквы, остальные символы — фиксированные литералы (дефис, пробел…).
 * data-raddle-script="latin" — только латиница; иначе кириллица.
 * Локальная проверка: node static/js/raddle_masked_input.test.js
 */
(function (global) {
  'use strict';

  var SLOT = '#';
  var CYRILLIC_LETTER_RE = /[а-яёА-ЯЁ]/;
  var CYRILLIC_EXTRACT_RE = /[а-яёА-ЯЁ]/g;
  var LATIN_LETTER_RE = /[a-zA-Z]/;
  var LATIN_EXTRACT_RE = /[a-zA-Z]/g;
  var MIXED_LETTER_RE = /[a-zA-Zа-яёА-ЯЁ]/;
  var MIXED_EXTRACT_RE = /[a-zA-Zа-яёА-ЯЁ]/g;
  var BOUND = 'data-raddle-mask-bound';
  var maskByInput = typeof WeakMap !== 'undefined' ? new WeakMap() : null;
  var maskByInputFallback = null;

  function slotCount(fmt) {
    var n = 0;
    for (var i = 0; i < fmt.length; i++) {
      if (fmt.charAt(i) === SLOT) n++;
    }
    return n;
  }

  function inputScript(input) {
    if (!input || typeof input.getAttribute !== 'function') return 'cyrillic';
    var s = input.getAttribute('data-raddle-script');
    if (s === 'latin' || s === 'mixed') return s;
    return 'cyrillic';
  }

  function isLatinScript(script) {
    return script === 'latin';
  }

  function isMixedScript(script) {
    return script === 'mixed';
  }

  function normalizeLetter(ch, script) {
    var s = String(ch || '');
    if (isLatinScript(script)) return s.toUpperCase();
    // mixed и cyrillic: ё→е для кириллицы, латиница просто upper
    return s.replace(/ё/gi, function (m) {
      return m === 'ё' ? 'е' : 'Е';
    }).toUpperCase();
  }

  function extractRussianLetters(text) {
    var m = String(text || '').match(CYRILLIC_EXTRACT_RE);
    if (!m) return '';
    return m.map(function (ch) { return normalizeLetter(ch, 'cyrillic'); }).join('');
  }

  function extractLatinLetters(text) {
    var m = String(text || '').match(LATIN_EXTRACT_RE);
    if (!m) return '';
    return m.map(function (ch) { return normalizeLetter(ch, 'latin'); }).join('');
  }

  function extractMixedLetters(text) {
    var m = String(text || '').match(MIXED_EXTRACT_RE);
    if (!m) return '';
    return m.map(function (ch) { return normalizeLetter(ch, 'mixed'); }).join('');
  }

  function extractLetters(text, script) {
    if (isLatinScript(script)) return extractLatinLetters(text);
    if (isMixedScript(script)) return extractMixedLetters(text);
    return extractRussianLetters(text);
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
        if (letters.length > need) out += ch;
      }
    }
    return out;
  }

  function getMaskInstance(input) {
    if (!input) return null;
    if (maskByInput) return maskByInput.get(input) || null;
    if (!maskByInputFallback) maskByInputFallback = new Map();
    return maskByInputFallback.get(input) || null;
  }

  function setMaskInstance(input, mask) {
    if (!input) return;
    if (maskByInput) {
      maskByInput.set(input, mask);
      return;
    }
    if (!maskByInputFallback) maskByInputFallback = new Map();
    maskByInputFallback.set(input, mask);
  }

  function letterReForScript(script) {
    if (isLatinScript(script)) return LATIN_LETTER_RE;
    if (isMixedScript(script)) return MIXED_LETTER_RE;
    return CYRILLIC_LETTER_RE;
  }

  function buildMaskOptions(fmt, script) {
    var letterRe = letterReForScript(script);
    return {
      mask: fmt,
      definitions: (function () {
        var defs = {};
        defs[SLOT] = letterRe;
        return defs;
      })(),
      prepareChar: function (ch) {
        return normalizeLetter(ch, script);
      },
      lazy: true,
    };
  }

  function getLetters(input) {
    var mask = getMaskInstance(input);
    if (mask) return mask.unmaskedValue || '';
    return input.dataset.raddleLetters || '';
  }

  function setLetters(input, letters, opts) {
    opts = opts || {};
    var fmt = input.getAttribute('data-raddle-format') || '';
    var script = inputScript(input);
    var max = slotCount(fmt);
    var clean = extractLetters(letters, script).slice(0, max);
    var mask = getMaskInstance(input);
    if (mask) {
      mask.unmaskedValue = clean;
      return clean;
    }
    input.dataset.raddleLetters = clean;
    input.value = lettersToDisplay(fmt, clean);
    try {
      input.setSelectionRange(input.value.length, input.value.length);
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
    if (typeof IMask === 'undefined') return;

    var script = inputScript(input);
    input.setAttribute(BOUND, '1');
    input.setAttribute('inputmode', 'text');
    input.setAttribute('autocapitalize', 'characters');
    input.removeAttribute('maxlength');

    var initial = input.getAttribute('value') || input.value || '';
    input.removeAttribute('value');

    var mask = IMask(input, buildMaskOptions(fmt, script));
    setMaskInstance(input, mask);

    function syncDataset() {
      input.dataset.raddleLetters = mask.unmaskedValue || '';
    }

    mask.on('accept', function () {
      syncDataset();
      if (typeof hooks.onChange === 'function') {
        hooks.onChange(input, mask.unmaskedValue || '');
      }
    });

    mask.on('complete', function () {
      syncDataset();
      if (typeof hooks.onComplete === 'function') {
        hooks.onComplete(input, mask.unmaskedValue || '');
      }
    });

    if (initial) {
      mask.unmaskedValue = extractLetters(initial, script);
    } else {
      syncDataset();
    }
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
    extractLatinLetters: extractLatinLetters,
    extractMixedLetters: extractMixedLetters,
    extractLetters: extractLetters,
    lettersToDisplay: lettersToDisplay,
    buildMaskOptions: buildMaskOptions,
    getLetters: getLetters,
    setLetters: setLetters,
    getSubmitValue: getSubmitValue,
    bindInput: bindInput,
    bindAll: bindAll,
  };
})(typeof window !== 'undefined' ? window : global);
